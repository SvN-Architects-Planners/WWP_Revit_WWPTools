# -*- coding: utf-8 -*-
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit.DB import (
    Color, FilteredElementCollector, LinePatternElement, LinePattern,
    LinePatternSegment, LinePatternSegmentType, BuiltInCategory, GraphicsStyleType,
    Transaction as RevitTransaction
)
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommandLinkId, TaskDialogResult
import os
import datetime
import traceback
from System.Collections.Generic import List
import WWP_uiUtils as ui

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument


# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR = os.path.dirname(__file__)

# Master workbook (can contain BOTH patterns + types as separate sheets)
MASTER_XLSX_PATH = r"N:\Library\Design Software\Autodesk\Revit\Standards\Line Type\MasterLineType.xlsx"
LOCAL_MASTER_XLSX_PATH = os.path.join(SCRIPT_DIR, "MasterLineType.xlsx")

# Prompt user to choose a workbook at runtime.
PROMPT_FOR_EXCEL_PATH = True

# Optional overrides (set to the exact worksheet tab name, or leave as None to auto-detect)
PATTERN_WORKSHEET_NAME = None
LINE_TYPES_WORKSHEET_NAME = None

# Existing line types overwrite behavior:
# - True: update all mismatched existing line types without prompting
# - False: skip all mismatched existing line types without prompting
UPDATE_EXISTING_LINE_TYPES = True

# Line Pattern (Excel)
PATTERN_XLSX_PATH = r"N:\Library\Design Software\Autodesk\Revit\Standards\Line Type\MasterLinePattern.xlsx"
LOCAL_PATTERN_XLSX_PATH = os.path.join(SCRIPT_DIR, "MasterLinePattern.xlsx")

# Line Type (Excel)
XLSX_PATH = r"N:\Library\Design Software\Autodesk\Revit\Standards\Line Type\MasterLineType.xlsx"
LOCAL_XLSX_PATH = os.path.join(SCRIPT_DIR, "MasterLineType.xlsx")


def _pick_first_existing_path(paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


def _choose_excel_workbook(default_path):
    """Prompt user to select an Excel workbook. Returns selected path or None."""
    initial_dir = ""
    file_name = ""
    try:
        if default_path:
            initial_dir = os.path.dirname(default_path)
            file_name = os.path.basename(default_path)
    except Exception:
        pass
    return ui.uiUtils_open_file_dialog(
        title="Select Line Types Excel Workbook",
        filter_text="Excel Files (*.xlsx;*.xlsm;*.xls)|*.xlsx;*.xlsm;*.xls|All Files (*.*)|*.*",
        multiselect=False,
        initial_directory=initial_dir or "",
    )


PATTERN_DATA_PATH = None
LINE_TYPES_DATA_PATH = None

DATA_START_ROW = 2  # Data starts at row 2 (header in row 1)
# Header is row 1, row 2 is the Solid pattern (skip), data starts at row 3
PATTERN_START_ROW = 3

# Column indices (0-based)
COL_NAME = 0        # A
COL_COMMENTS = 1    # B
COL_WEIGHT = 2      # C
COL_PATTERN = 3     # D
COL_R = 4           # E
COL_G = 5           # F
COL_B = 6           # G
COL_PRESENT = 7     # H


# ============================================================
# Helper Functions
# ============================================================
def rgb_to_color(r, g, b):
    """Convert RGB values to Revit Color object"""
    try:
        return Color(int(r), int(g), int(b))
    except:
        return Color(0, 0, 0)  # Default to black


def _coerce_line_weight(raw_weight):
    """Coerce a cell value into a valid Revit line weight integer (usually 1-16)."""
    try:
        w = int(round(float(raw_weight)))
    except Exception:
        return 1
    if w < 1:
        return 1
    if w > 16:
        return 16
    return w


def _coerce_int(raw_value, default_value=0, min_value=None, max_value=None):
    """Coerce a cell value into an int.

    Excel commonly yields floats (e.g. 255.0) so this accepts 255, 255.0, "255", "255.0", etc.
    """
    if raw_value is None:
        return default_value

    try:
        s = str(raw_value).strip()
    except Exception:
        s = ""

    if s == "":
        return default_value

    try:
        v = int(round(float(s)))
    except Exception:
        return default_value

    if min_value is not None and v < min_value:
        v = min_value
    if max_value is not None and v > max_value:
        v = max_value
    return v


def _iter_excel_rows(path, worksheet_name=None):
    """Yield Excel sheet rows as list[str] using late-bound Excel COM.

    Requirements:
    - Microsoft Excel installed on the machine running Revit.

    Notes:
    - Uses UsedRange (can include trailing empty columns/rows depending on file).
    """
    try:
        clr.AddReference('System')
        from System import Activator, Type
        from System.Runtime.InteropServices import Marshal
        Reflection = __import__('System.Reflection', fromlist=['BindingFlags'])
        BindingFlags = Reflection.BindingFlags
    except Exception as e:
        raise Exception("Excel COM not available. ({})".format(str(e)))

    _BASE_FLAGS = BindingFlags.Public | BindingFlags.Instance | BindingFlags.OptionalParamBinding

    def _com_get(obj, name):
        # Some COM "properties" are actually methods.
        try:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.GetProperty, None, obj, None)
        except Exception:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, [])

    def _com_set(obj, name, value):
        try:
            obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.SetProperty, None, obj, [value])
        except Exception:
            # Some COM properties behave like methods
            obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, [value])

    def _com_call(obj, name, *args):
        # Some COM "methods" are actually properties with parameters (Item)
        try:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, list(args))
        except Exception:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.GetProperty, None, obj, list(args))

    excel_app = None
    workbook = None
    worksheet = None
    used_range = None

    try:
        excel_type = Type.GetTypeFromProgID('Excel.Application')
        if excel_type is None:
            raise Exception("Excel COM ProgID not found (is Microsoft Excel installed?)")
        excel_app = Activator.CreateInstance(excel_type)
        _com_set(excel_app, 'Visible', False)
        _com_set(excel_app, 'DisplayAlerts', False)

        workbooks = _com_get(excel_app, 'Workbooks')
        # Prefer the simplest signature to avoid COM overload resolution issues.
        try:
            workbook = _com_call(workbooks, 'Open', path)
        except Exception:
            workbook = _com_call(workbooks, 'Open', path, 0, True)

        worksheets = _com_get(workbook, 'Worksheets')
        ws_key = 1 if (worksheet_name is None or worksheet_name == "") else worksheet_name
        worksheet = _com_call(worksheets, 'Item', ws_key)

        used_range = _com_get(worksheet, 'UsedRange')
        rows = _com_get(used_range, 'Rows')
        cols = _com_get(used_range, 'Columns')
        row_count = int(_com_get(rows, 'Count'))
        col_count = int(_com_get(cols, 'Count'))
        values = _com_get(used_range, 'Value2')

        # Value2 returns either a scalar (single cell) or a 2D System.Array
        is_array = hasattr(values, 'GetValue')
        is_2d = False
        if is_array:
            try:
                is_2d = int(values.Rank) == 2
            except Exception:
                is_2d = False
        if is_2d:
            r0 = int(values.GetLowerBound(0))
            c0 = int(values.GetLowerBound(1))

        for r in range(1, row_count + 1):
            row = []
            for c in range(1, col_count + 1):
                if is_2d:
                    try:
                        v = values.GetValue(r0 + (r - 1), c0 + (c - 1))
                    except Exception:
                        v = None
                else:
                    # single cell sheet
                    v = values if (r == 1 and c == 1) else None

                if v is None:
                    row.append("")
                else:
                    try:
                        row.append(str(v))
                    except Exception:
                        row.append("{}".format(v))
            yield row

    finally:
        # Close and release COM objects in reverse order.
        try:
            if workbook is not None:
                workbook.Close(False)
        except Exception:
            pass

        try:
            if excel_app is not None:
                excel_app.Quit()
        except Exception:
            pass

        try:
            if used_range is not None:
                Marshal.ReleaseComObject(used_range)
        except Exception:
            pass
        try:
            if worksheet is not None:
                Marshal.ReleaseComObject(worksheet)
        except Exception:
            pass
        try:
            if workbook is not None:
                Marshal.ReleaseComObject(workbook)
        except Exception:
            pass
        try:
            if excel_app is not None:
                Marshal.ReleaseComObject(excel_app)
        except Exception:
            pass


def _iter_table_rows(path, worksheet_name=None):
    ext = os.path.splitext(path)[1].lower()
    if ext in ['.xlsx', '.xlsm', '.xls']:
        return _iter_excel_rows(path, worksheet_name=worksheet_name)
    raise Exception("Only Excel files are supported (.xlsx/.xlsm/.xls)")


def _normalize_header_cell(v):
    try:
        return str(v).strip().lower()
    except Exception:
        return ""


def _detect_excel_sheet_names(path):
    """Return (pattern_sheet, line_types_sheet) by inspecting headers.

    If detection fails, falls back to (1st, 2nd) when possible.
    """
    try:
        clr.AddReference('System')
        from System import Activator, Type
        from System.Runtime.InteropServices import Marshal
        Reflection = __import__('System.Reflection', fromlist=['BindingFlags'])
        BindingFlags = Reflection.BindingFlags
    except Exception as e:
        raise Exception("Excel COM not available for sheet detection. ({})".format(str(e)))

    _BASE_FLAGS = BindingFlags.Public | BindingFlags.Instance | BindingFlags.OptionalParamBinding

    def _com_get(obj, name):
        try:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.GetProperty, None, obj, None)
        except Exception:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, [])

    def _com_set(obj, name, value):
        try:
            obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.SetProperty, None, obj, [value])
        except Exception:
            obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, [value])

    def _com_call(obj, name, *args):
        try:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.InvokeMethod, None, obj, list(args))
        except Exception:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.GetProperty, None, obj, list(args))

    excel_app = None
    workbook = None

    # Heuristics: required headers (lowercase)
    pattern_required = set(['name', 'style', 'length'])
    types_required = set(['name', 'weight', 'pattern', 'r', 'g', 'b'])

    best_pattern = None
    best_types = None
    best_pattern_score = -1
    best_types_score = -1

    try:
        excel_type = Type.GetTypeFromProgID('Excel.Application')
        if excel_type is None:
            raise Exception("Excel COM ProgID not found (is Microsoft Excel installed?)")
        excel_app = Activator.CreateInstance(excel_type)
        _com_set(excel_app, 'Visible', False)
        _com_set(excel_app, 'DisplayAlerts', False)

        workbooks = _com_get(excel_app, 'Workbooks')
        try:
            workbook = _com_call(workbooks, 'Open', path)
        except Exception:
            workbook = _com_call(workbooks, 'Open', path, 0, True)
        worksheets = _com_get(workbook, 'Worksheets')
        sheet_count = int(_com_get(worksheets, 'Count'))

        for i in range(1, sheet_count + 1):
            ws = None
            used = None
            try:
                ws = _com_call(worksheets, 'Item', i)
                ws_name = ""
                try:
                    ws_name = str(_com_get(ws, 'Name')).strip().lower()
                except Exception:
                    ws_name = ""
                used = _com_get(ws, 'UsedRange')
                values = _com_get(used, 'Value2')
                cols = _com_get(used, 'Columns')
                rows = _com_get(used, 'Rows')
                col_count = int(_com_get(cols, 'Count'))
                row_count = int(_com_get(rows, 'Count'))

                is_array = hasattr(values, 'GetValue')
                is_2d = False
                if is_array:
                    try:
                        is_2d = int(values.Rank) == 2
                    except Exception:
                        is_2d = False
                if is_2d:
                    r0 = int(values.GetLowerBound(0))
                    c0 = int(values.GetLowerBound(1))

                # Scan first few rows for a header row
                scan_rows = min(5, row_count)
                for r in range(1, scan_rows + 1):
                    headers = set()
                    for c in range(1, col_count + 1):
                        cell = None
                        try:
                            if is_2d:
                                cell = values.GetValue(r0 + (r - 1), c0 + (c - 1))
                            else:
                                cell = values if (r == 1 and c == 1) else None
                        except Exception:
                            cell = None
                        hv = _normalize_header_cell(cell)
                        if hv:
                            headers.add(hv)

                    if not headers:
                        continue

                    p_score = len(pattern_required.intersection(headers))
                    t_score = len(types_required.intersection(headers))

                    # Bonus points if sheet name indicates intent.
                    if 'pattern' in ws_name:
                        p_score += 2
                    if 'type' in ws_name or 'linetype' in ws_name or 'line type' in ws_name:
                        t_score += 2

                    if p_score > best_pattern_score:
                        best_pattern_score = p_score
                        best_pattern = _com_get(ws, 'Name')
                    if t_score > best_types_score:
                        best_types_score = t_score
                        best_types = _com_get(ws, 'Name')

                    # If we got perfect matches, no need to scan further rows for this sheet.
                    if p_score == len(pattern_required) or t_score == len(types_required):
                        break

            finally:
                try:
                    if used is not None:
                        Marshal.ReleaseComObject(used)
                except Exception:
                    pass
                try:
                    if ws is not None:
                        Marshal.ReleaseComObject(ws)
                except Exception:
                    pass

        # Decide fallbacks
        if sheet_count >= 2:
            if best_pattern_score <= 0:
                best_pattern = _com_get(_com_call(worksheets, 'Item', 1), 'Name')
            if best_types_score <= 0:
                # Prefer second sheet for types if ambiguous
                best_types = _com_get(_com_call(worksheets, 'Item', 2), 'Name')
        else:
            # Only one sheet
            if best_pattern_score <= 0:
                best_pattern = _com_get(_com_call(worksheets, 'Item', 1), 'Name')
            if best_types_score <= 0:
                best_types = _com_get(_com_call(worksheets, 'Item', 1), 'Name')

        return best_pattern, best_types

    finally:
        try:
            if workbook is not None:
                workbook.Close(False)
        except Exception:
            pass
        try:
            if excel_app is not None:
                excel_app.Quit()
        except Exception:
            pass
        try:
            if workbook is not None:
                Marshal.ReleaseComObject(workbook)
        except Exception:
            pass
        try:
            if excel_app is not None:
                Marshal.ReleaseComObject(excel_app)
        except Exception:
            pass


def _split_to_list(raw_str):
    """Split a delimited string into tokens using comma."""
    cleaned = raw_str.strip()
    return [p for p in cleaned.split(',') if p.strip() != ""]


def parse_pattern_tokens(pattern_str):
    """Parse pattern tokens like 'dash,space,dash,space' into segment type strings."""
    if not pattern_str or pattern_str.strip() == "":
        return []
    return [p.strip().lower() for p in _split_to_list(pattern_str)]


def parse_length_list(length_str):
    """Parse length list '6,3,12,3' into floats (strips units)."""
    if not length_str or length_str.strip() == "":
        return []
    values = []
    for token in _split_to_list(length_str):
        t = token
        for unit in ["mm", "in", "\""]:
            if t.lower().endswith(unit):
                t = t[:-len(unit)]
                break
        try:
            values.append(float(t))
        except:
            return []
    return values


def build_segments(tokens, lengths):
    """Create LinePatternSegment list from tokens and lengths."""
    if not tokens or not lengths or len(tokens) != len(lengths):
        return None
    segs = List[LinePatternSegment]()
    for token, length in zip(tokens, lengths):
        t = token.lower()
        if t in ["dash", "d"]:
            seg_type = LinePatternSegmentType.Dash
        elif t in ["dot", "point", "p"]:
            seg_type = LinePatternSegmentType.Dot
        else:  # treat anything else as space
            seg_type = LinePatternSegmentType.Space
        segs.Add(LinePatternSegment(seg_type, length))
    return segs


def get_line_pattern_by_name(doc, name):
    """Get line pattern element by name"""
    collector = FilteredElementCollector(doc)
    line_patterns = collector.OfClass(LinePatternElement).ToElements()
    
    for lp in line_patterns:
        if lp.Name == name:
            return lp
    
    return None


def get_line_type_by_name(doc, name):
    """Get graphics style (line type) by name from Lines category"""
    try:
        lines_cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
        for subcat in lines_cat.SubCategories:
            if subcat.Name == name:
                return subcat
    except:
        pass
    return None


def compare_line_properties(line_type, expected_weight, expected_color):
    """Compare existing line type properties with expected values."""
    differences = []

    # Weight (Projection)
    try:
        current_weight = line_type.GetLineWeight(GraphicsStyleType.Projection)
    except Exception:
        current_weight = None

    if current_weight is None or current_weight != expected_weight:
        msg = "Weight (Projection): {} -> {}".format(current_weight, expected_weight)
        differences.append(msg)

    # Color
    try:
        current_color = line_type.LineColor
        if (current_color.Red != expected_color.Red or
            current_color.Green != expected_color.Green or
            current_color.Blue != expected_color.Blue):
            msg = "Color: RGB({},{},{}) -> RGB({},{},{})".format(
                current_color.Red, current_color.Green, current_color.Blue,
                expected_color.Red, expected_color.Green, expected_color.Blue)
            differences.append(msg)
    except Exception:
        differences.append("Color: <unreadable> -> RGB({},{},{})".format(
            expected_color.Red, expected_color.Green, expected_color.Blue))
    
    return differences


# ============================================================
# Main Script
# ============================================================
default_workbook = _pick_first_existing_path([
    MASTER_XLSX_PATH,
    LOCAL_MASTER_XLSX_PATH,
    XLSX_PATH,
    LOCAL_XLSX_PATH,
    PATTERN_XLSX_PATH,
    LOCAL_PATTERN_XLSX_PATH,
])

selected_workbook = None
if PROMPT_FOR_EXCEL_PATH:
    selected_workbook = _choose_excel_workbook(default_workbook or MASTER_XLSX_PATH)

if not selected_workbook:
    # User canceled; fall back to default if it exists.
    selected_workbook = default_workbook

if not selected_workbook or not os.path.exists(selected_workbook):
    ui.uiUtils_alert(
        "Excel workbook not found.\n\nDefault:\n{}\n\nYou can browse to a custom workbook in the file dialog.".format(
            default_workbook or MASTER_XLSX_PATH
        ),
        title="Error"
    )
    raise Exception("Excel workbook not found")

# Use the selected workbook for BOTH patterns and types.
PATTERN_DATA_PATH = selected_workbook
LINE_TYPES_DATA_PATH = selected_workbook


# If both steps point at the same Excel workbook, detect sheet names once.
_pattern_ws = PATTERN_WORKSHEET_NAME
_types_ws = LINE_TYPES_WORKSHEET_NAME
try:
    if (os.path.splitext(PATTERN_DATA_PATH)[1].lower() in ['.xlsx', '.xlsm', '.xls'] and
        os.path.splitext(LINE_TYPES_DATA_PATH)[1].lower() in ['.xlsx', '.xlsm', '.xls'] and
        os.path.abspath(PATTERN_DATA_PATH).lower() == os.path.abspath(LINE_TYPES_DATA_PATH).lower() and
        (_pattern_ws is None or _types_ws is None)):
        detected_pattern_ws, detected_types_ws = _detect_excel_sheet_names(PATTERN_DATA_PATH)
        if _pattern_ws is None:
            _pattern_ws = detected_pattern_ws
        if _types_ws is None:
            _types_ws = detected_types_ws
except Exception as _sheet_detect_err:
    # Non-fatal: will fall back to first sheet when iterating.
    print("WARNING: Could not auto-detect worksheet names: {}".format(str(_sheet_detect_err)))

try:
    print("Line Pattern & Type Import Tool - Starting...")
    print("Started: {}".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    
    # ============================================================
    # STEP 1: Import Line Patterns
    # ============================================================
    print("\n=== STEP 1: IMPORTING LINE PATTERNS ===")
    print("Loading Pattern Source: {}".format(PATTERN_DATA_PATH))
    if os.path.splitext(PATTERN_DATA_PATH)[1].lower() in ['.xlsx', '.xlsm', '.xls']:
        print("Using Pattern Worksheet: {}".format(_pattern_ws if _pattern_ws else "<first sheet>"))
    
    line_patterns_data = []
    pattern_row_count = 0
    
    try:
        for row_idx, row in enumerate(_iter_table_rows(PATTERN_DATA_PATH, worksheet_name=_pattern_ws), start=1):
            print("Row {}: Raw row = {}".format(row_idx, row))

            if row_idx < PATTERN_START_ROW:  # Skip header and solid row
                print("  -> Skipped (before PATTERN_START_ROW={})".format(PATTERN_START_ROW))
                continue
            if not row or not row[0].strip():
                print("Row {}: Empty - stopping".format(row_idx))
                break

            pattern_row_count += 1
            try:
                pattern_name = str(row[0]).strip()
                    # The style column can contain comma-delimited tokens; join mid columns into style.
                if len(row) >= 3:
                    pattern_style_raw = ",".join(row[1:-1]).strip()
                    pattern_length_raw = str(row[-1]).strip()
                elif len(row) == 2:
                    pattern_style_raw = str(row[1]).strip()
                    pattern_length_raw = ""
                else:
                    pattern_style_raw = ""
                    pattern_length_raw = ""

                print("  Name='{}', Style='{}', Length='{}'".format(pattern_name, pattern_style_raw, pattern_length_raw))

                pattern_tokens = parse_pattern_tokens(pattern_style_raw)
                pattern_lengths = parse_length_list(pattern_length_raw)
                print("  Tokens={}, Lengths={}".format(pattern_tokens, pattern_lengths))

                segments = build_segments(pattern_tokens, pattern_lengths)

                if segments:
                    line_patterns_data.append({
                        'name': pattern_name,
                        'tokens': pattern_tokens,
                        'lengths': pattern_lengths,
                        'segments': segments
                    })
                    print("  -> Created segments: {}".format(pattern_tokens))
                else:
                    print("  -> WARNING: Invalid pattern/length pair, skipped")
            except Exception as e:
                print("  -> ERROR - {}".format(str(e)))
                continue
        
        print("Successfully read {} line pattern(s)".format(pattern_row_count))
    except Exception as e:
        print("ERROR reading Pattern source: {}".format(str(e)))
        raise
    
    # Import line patterns
    if line_patterns_data:
        print("\nStarting transaction for line patterns...")
        t_pattern = RevitTransaction(doc, "Import Line Patterns")
        t_pattern.Start()
        
        pattern_added_count = 0
        pattern_skipped_count = 0
        pattern_conflicts = []
        
        try:
            for idx, pattern_data in enumerate(line_patterns_data, 1):
                pattern_name = pattern_data['name']
                segments = pattern_data['segments']
                
                print("{}. Processing pattern: {}".format(idx, pattern_name))
                
                existing_pattern = get_line_pattern_by_name(doc, pattern_name)
                
                if existing_pattern:
                    print("  Skipped - pattern already exists")
                    pattern_skipped_count += 1
                else:
                    try:
                        lp = LinePattern(pattern_name)
                        lp.SetSegments(segments)
                        LinePatternElement.Create(doc, lp)
                        print("  Created new line pattern")
                        pattern_added_count += 1
                    except Exception as e:
                        print("  ERROR creating pattern: {}".format(str(e)))
                        pattern_conflicts.append((pattern_name, str(e)))
            
            print("Committing pattern transaction...")
            t_pattern.Commit()
            print("Pattern transaction committed successfully")
            
            print("\n=== PATTERN IMPORT SUMMARY ===")
            print("Added: {} new pattern(s)".format(pattern_added_count))
            print("Skipped: {} pattern(s)".format(pattern_skipped_count))
            if pattern_conflicts:
                print("Errors: {}".format(len(pattern_conflicts)))
                for name, error in pattern_conflicts:
                    print("  {}: {}".format(name, error))
        
        except Exception as e:
            print("Pattern transaction failed: {}".format(str(e)))
            t_pattern.RollBack()
            print("Pattern rollback complete")
            ui.uiUtils_alert("Pattern import failed:\n{}".format(str(e)), title="Error")
            raise
    else:
        print("No line patterns to import")
    
    # ============================================================
    # STEP 2: Import Line Types
    # ============================================================
    print("\n=== STEP 2: IMPORTING LINE TYPES ===")
    print("Loading Line Type Source: {}".format(LINE_TYPES_DATA_PATH))
    if os.path.splitext(LINE_TYPES_DATA_PATH)[1].lower() in ['.xlsx', '.xlsm', '.xls']:
        print("Using Line Types Worksheet: {}".format(_types_ws if _types_ws else "<first sheet>"))
    
    # Collect line type data from source
    line_types_data = []
    row_count = 0
    
    try:
        for row_idx, row in enumerate(_iter_table_rows(LINE_TYPES_DATA_PATH, worksheet_name=_types_ws), start=1):
            if row_idx < DATA_START_ROW:
                continue  # Skip header
            if not row or not row[COL_NAME].strip():
                print("Row {}: Empty - stopping".format(row_idx))
                break

            row_count += 1
            try:
                lt_entry = {
                    'name': str(row[COL_NAME]).strip(),
                    'comments': str(row[COL_COMMENTS]).strip() if len(row) > COL_COMMENTS and row[COL_COMMENTS] else "",
                    'weight': _coerce_line_weight(row[COL_WEIGHT]) if len(row) > COL_WEIGHT and row[COL_WEIGHT] else 1,
                    'pattern': str(row[COL_PATTERN]).strip() if len(row) > COL_PATTERN and row[COL_PATTERN] else "",
                    'r': _coerce_int(row[COL_R], 0, 0, 255) if len(row) > COL_R else 0,
                    'g': _coerce_int(row[COL_G], 0, 0, 255) if len(row) > COL_G else 0,
                    'b': _coerce_int(row[COL_B], 0, 0, 255) if len(row) > COL_B else 0,
                    'present': str(row[COL_PRESENT]).strip().lower() if len(row) > COL_PRESENT and row[COL_PRESENT] else "",
                }
                line_types_data.append(lt_entry)
                print("Row {}: {} - Weight: {}, RGB({},{},{})".format(
                    row_idx, lt_entry['name'], lt_entry['weight'],
                    lt_entry['r'], lt_entry['g'], lt_entry['b']))
            except Exception as e:
                print("Row {}: ERROR - {}".format(row_idx, str(e)))
                continue
        
        print("Successfully read {} line type(s)".format(row_count))
    except Exception as e:
        print("ERROR reading Line Type source: {}".format(str(e)))
        raise
    
    if not line_types_data:
        ui.uiUtils_alert("No line type data found in source file", title="Warning")
        raise Exception("No line type data found")
    
    # Start transaction
    print("Starting transaction in document: {}".format(doc.Title))
    
    t = RevitTransaction(doc, "Import Line Types")
    t.Start()
    print("Transaction started")
    print("Processing {} line types...".format(len(line_types_data)))
    
    added_count = 0
    updated_count = 0
    skipped_count = 0
    conflicts = []
    
    try:
        for idx, lt_data in enumerate(line_types_data, 1):
            lt_name = lt_data['name']
            expected_weight = lt_data['weight']
            expected_pattern = lt_data['pattern']
            expected_color = rgb_to_color(lt_data['r'], lt_data['g'], lt_data['b'])
            
            print("{}. Processing: {}".format(idx, lt_name))
            
            # Check if line type exists
            try:
                existing_lt = get_line_type_by_name(doc, lt_name)
            except Exception as e:
                print("  ERROR checking existence: {}".format(str(e)))
                conflicts.append((lt_name, "Check error: {}".format(str(e))))
                continue
            
            if existing_lt:
                # Line type exists - check properties
                try:
                    differences = compare_line_properties(existing_lt, expected_weight, expected_color)
                except Exception as e:
                    print("  ERROR comparing properties: {}".format(str(e)))
                    conflicts.append((lt_name, "Compare error: {}".format(str(e))))
                    continue
                
                if not differences:
                    # Properties match - skip
                    print("  Skipped - properties match")
                    skipped_count += 1
                else:
                    # Properties differ - apply configured policy (no per-item prompts)
                    if UPDATE_EXISTING_LINE_TYPES:
                        try:
                            existing_lt.SetLineWeight(int(expected_weight), GraphicsStyleType.Projection)
                            existing_lt.LineColor = expected_color
                            print("  Updated properties")
                            updated_count += 1
                        except Exception as e:
                            print("  ERROR updating: {}".format(str(e)))
                            conflicts.append((lt_name, str(e)))
                    else:
                        print("  Skipped - existing differs (overwrite disabled)")
                        skipped_count += 1
            else:
                # Line type doesn't exist - create it
                try:
                    lines_cat = doc.Settings.Categories.get_Item(BuiltInCategory.OST_Lines)
                    new_style = doc.Settings.Categories.NewSubcategory(lines_cat, lt_name)

                    if expected_pattern:
                        pattern_element = get_line_pattern_by_name(doc, expected_pattern)
                        if pattern_element:
                            new_style.SetLinePatternId(pattern_element.Id, GraphicsStyleType.Projection)
                            print("  Using pattern '{}'".format(expected_pattern))
                        else:
                            print("  WARNING: Pattern '{}' not found - created as Solid".format(expected_pattern))
                            conflicts.append((lt_name, "Pattern '{}' not found; created Solid".format(expected_pattern)))

                    new_style.SetLineWeight(int(expected_weight), GraphicsStyleType.Projection)
                    new_style.LineColor = expected_color
                    print("  Created new line style")
                    added_count += 1
                except Exception as e:
                    print("  ERROR creating: {}".format(str(e)))
                    conflicts.append((lt_name, str(e)))
        
        print("Committing transaction...")
        t.Commit()
        print("Transaction committed successfully")
        
        # Summary
        print("\n=== SUMMARY ===")
        print("Added: {} new line type(s)".format(added_count))
        print("Updated: {} line type(s)".format(updated_count))
        print("Skipped: {} line type(s)".format(skipped_count))
        print("Total Processed: {}".format(added_count + updated_count + skipped_count))
        
        if conflicts:
            print("\nErrors Encountered: {}".format(len(conflicts)))
            for name, error in conflicts:
                print("  {}: {}".format(name, error))
        
        print("Completed: {}".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        
        # Show summary dialog
        summary_msg = "Import Complete!\n\nAdded: {}\nUpdated: {}\nSkipped: {}".format(added_count, updated_count, skipped_count)
        if conflicts:
            summary_msg += "\n\nErrors: {}".format(len(conflicts))
        
        TaskDialog.Show("Line Type Import", summary_msg)
        
    except Exception as e:
        print("Transaction failed: {}".format(str(e)))
        t.RollBack()
        print("Rollback complete")
        ui.uiUtils_alert("Transaction failed:\n{}".format(str(e)), title="Error")
        raise
    
except Exception as e:
    print("FATAL ERROR: {}".format(str(e)))
    print("Error Type: {}".format(type(e).__name__))
    try:
        print(traceback.format_exc())
    except Exception:
        pass
    TaskDialog.Show("Error", "Failed to process source file:\n{}".format(str(e)))
