import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import BuiltInCategory, InstanceBinding, TypeBinding, Transaction
from Autodesk.Revit.UI import TaskDialog
import os
import datetime
import traceback
import sys
import WWP_uiUtils as ui

clr.AddReference('System')
from System import Enum
from System import Array, Object

doc = __revit__.ActiveUIDocument.Document
app = doc.Application

# ============================================================
# Configuration
# ============================================================
SCRIPT_DIR = os.path.dirname(__file__)

DEFAULT_XLSX_PATH = r"N:\Library\Design Software\Autodesk\Revit\Shared Parameters\Shared Parameters Import.xlsx"
DEFAULT_SHEET_NAME = "Project Parameters"

DEFAULT_SHARED_PARAMETERS_PATH = r"N:\Library\Design Software\Autodesk\Revit\Shared Parameters\SharedParameters.txt"

DATA_START_ROW = 2  # Header in row 1

# Determine parameter group support (BuiltInParameterGroup vs GroupTypeId)
_USE_BIP_GROUPS = hasattr(DB, "BuiltInParameterGroup")
_INVALID_GROUP = DB.BuiltInParameterGroup.INVALID if _USE_BIP_GROUPS else getattr(DB.GroupTypeId, "Invalid", None)
_GROUP_MAP = {}
if not _USE_BIP_GROUPS:
    for name in dir(DB.GroupTypeId):
        if name.startswith("_"):
            continue
        try:
            _GROUP_MAP[name.lower()] = getattr(DB.GroupTypeId, name)
        except Exception:
            continue
    # Manual aliases for legacy BuiltInParameterGroup names
    _ALIASES = {
        "pg_title": "Title",
        "pg_text": "Text",
        "pg_identity_data": "IdentityData",
    }
    for key, gt_name in _ALIASES.items():
        try:
            val = getattr(DB.GroupTypeId, gt_name)
            _GROUP_MAP[key] = val
        except Exception:
            continue



# ============================================================
# Excel Helpers (COM)
# ============================================================
def _pick_first_existing_path(paths):
    for p in paths:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


def _cancel_import(message):
    TaskDialog.Show("Cancelled", message)
    print(message)
    sys.exit()


def _is_url(path):
    return bool(path) and path.startswith(("http://", "https://"))


def _download_url_to_temp(url, suffix=".xlsx"):
    import tempfile
    import urllib.request
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(tmp_fd)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as response, open(tmp_path, "wb") as f:
        f.write(response.read())
    return tmp_path


def _load_xaml_window(xaml_name):
    try:
        clr.AddReference('PresentationFramework')
        clr.AddReference('PresentationCore')
        clr.AddReference('WindowsBase')
    except Exception:
        pass
    from System.Windows.Markup import XamlReader
    from System.IO import StringReader
    from System.Xml import XmlReader
    xaml_path = os.path.join(SCRIPT_DIR, xaml_name)
    with open(xaml_path, "r") as f:
        xaml_text = f.read()
    reader = XmlReader.Create(StringReader(xaml_text))
    return XamlReader.Load(reader)


def _browse_file(title, filter_text, initial_path=""):
    from Microsoft.Win32 import OpenFileDialog
    dlg = OpenFileDialog()
    dlg.Title = title
    dlg.Filter = filter_text
    dlg.Multiselect = False
    dlg.CheckFileExists = True
    initial_dir = ""
    try:
        if initial_path:
            initial_dir = os.path.dirname(initial_path)
    except Exception:
        pass
    if initial_dir and os.path.isdir(initial_dir):
        dlg.InitialDirectory = initial_dir
    if dlg.ShowDialog() == True:
        return str(dlg.FileName or "")
    return None


def _show_import_dialog(default_xlsx, default_shared):
    window = _load_xaml_window("ImportProjectParametersDialog.xaml")

    try:
        from System.Diagnostics import Process
        from System.Windows.Interop import WindowInteropHelper
        from System import IntPtr
        hwnd = Process.GetCurrentProcess().MainWindowHandle
        if hwnd and hwnd != IntPtr.Zero:
            WindowInteropHelper(window).Owner = hwnd
    except Exception:
        pass

    excel_box = window.FindName("ExcelPathBox")
    shared_box = window.FindName("SharedParamPathBox")
    browse_excel_btn = window.FindName("BrowseExcelButton")
    browse_shared_btn = window.FindName("BrowseSharedParamButton")
    ok_btn = window.FindName("OkButton")
    cancel_btn = window.FindName("CancelButton")

    if excel_box is not None:
        excel_box.Text = default_xlsx or ""
    if shared_box is not None:
        shared_box.Text = default_shared or ""

    def on_browse_excel(sender, e):
        path = _browse_file(
            "Select Project Parameters Excel Workbook",
            "Excel Files (*.xlsx;*.xlsm;*.xls)|*.xlsx;*.xlsm;*.xls|All Files (*.*)|*.*",
            str(excel_box.Text or ""),
        )
        if path:
            excel_box.Text = path

    def on_browse_shared(sender, e):
        path = _browse_file(
            "Select Shared Parameters File",
            "Text Files (*.txt)|*.txt|All Files (*.*)|*.*",
            str(shared_box.Text or ""),
        )
        if path:
            shared_box.Text = path

    def on_ok(sender, e):
        window.DialogResult = True

    def on_cancel(sender, e):
        window.DialogResult = False

    if browse_excel_btn is not None:
        browse_excel_btn.Click += on_browse_excel
    if browse_shared_btn is not None:
        browse_shared_btn.Click += on_browse_shared
    if ok_btn is not None:
        ok_btn.Click += on_ok
    if cancel_btn is not None:
        cancel_btn.Click += on_cancel

    if window.ShowDialog() != True:
        return None, None

    xlsx = str(excel_box.Text or "").strip() if excel_box is not None else ""
    shared = str(shared_box.Text or "").strip() if shared_box is not None else ""
    return xlsx or None, shared or None


def _iter_excel_rows(path, worksheet_name=None):
    try:
        clr.AddReference('System')
        from System import Activator, Type
        from System.Runtime.InteropServices import Marshal
        Reflection = __import__('System.Reflection', fromlist=['BindingFlags'])
        BindingFlags = Reflection.BindingFlags
    except Exception as e:
        raise Exception("Excel COM not available. ({})".format(str(e)))

    _BASE_FLAGS = BindingFlags.Public | BindingFlags.Instance | BindingFlags.OptionalParamBinding

    def _to_object_array(args):
        if not args:
            return Array[Object]([])
        return Array[Object](tuple(args))

    def _com_get(obj, name):
        try:
            return obj.GetType().InvokeMember(name, _BASE_FLAGS | BindingFlags.GetProperty, None, obj, None)
        except Exception:
            return obj.GetType().InvokeMember(
                name,
                _BASE_FLAGS | BindingFlags.InvokeMethod,
                None,
                obj,
                _to_object_array(()),
            )

    def _com_set(obj, name, value):
        try:
            obj.GetType().InvokeMember(
                name,
                _BASE_FLAGS | BindingFlags.SetProperty,
                None,
                obj,
                _to_object_array((value,)),
            )
        except Exception:
            obj.GetType().InvokeMember(
                name,
                _BASE_FLAGS | BindingFlags.InvokeMethod,
                None,
                obj,
                _to_object_array((value,)),
            )

    def _com_call(obj, name, *args):
        try:
            return obj.GetType().InvokeMember(
                name,
                _BASE_FLAGS | BindingFlags.InvokeMethod,
                None,
                obj,
                _to_object_array(args),
            )
        except Exception:
            return obj.GetType().InvokeMember(
                name,
                _BASE_FLAGS | BindingFlags.GetProperty,
                None,
                obj,
                _to_object_array(args),
            )

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
        try:
            workbook = _com_call(workbooks, 'Open', path)
        except Exception:
            workbook = _com_call(workbooks, 'Open', path, 0, True)

        worksheets = _com_get(workbook, 'Worksheets')
        ws_key = 1
        if worksheet_name:
            try:
                worksheet = _com_call(worksheets, 'Item', worksheet_name)
            except Exception:
                worksheet = None
            if worksheet is None:
                raise Exception("Worksheet '{}' not found".format(worksheet_name))
        if worksheet is None:
            worksheet = _com_call(worksheets, 'Item', ws_key)

        used_range = _com_get(worksheet, 'UsedRange')
        rows = _com_get(used_range, 'Rows')
        cols = _com_get(used_range, 'Columns')
        row_count = int(_com_get(rows, 'Count'))
        col_count = int(_com_get(cols, 'Count'))
        values = _com_get(used_range, 'Value2')

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


def _normalize_header_cell(v):
    try:
        return str(v).strip().lower()
    except Exception:
        return ""


def _build_header_map(header_row):
    header_map = {}
    for idx, cell in enumerate(header_row):
        key = _normalize_header_cell(cell)
        if key and key not in header_map:
            header_map[key] = idx
    return header_map


# ============================================================
# Revit Helpers
# ============================================================
def _try_parse_built_in_category(name):
    if not name:
        return None
    try:
        return Enum.Parse(BuiltInCategory, name.strip(), True)
    except Exception:
        return None


def _try_parse_param_group(name):
    if _USE_BIP_GROUPS:
        if not name:
            return _INVALID_GROUP
        raw = name.strip()
        if raw.startswith("BuiltInParameterGroup."):
            raw = raw.split(".", 1)[1]
        try:
            return Enum.Parse(DB.BuiltInParameterGroup, raw, True)
        except Exception:
            return _INVALID_GROUP
    else:
        if not name:
            return None
        raw = name.strip()
        key = raw.lower()
        # Accept fully-qualified like GroupTypeId.Name as well
        if key.startswith("grouptypeid."):
            key = key.split(".", 1)[1]
        return _GROUP_MAP.get(key, None)


def _get_shared_definition(def_file, group_name, param_name):
    if def_file is None:
        return None
    if group_name:
        try:
            grp = def_file.Groups.get_Item(group_name)
            if grp:
                return grp.Definitions.get_Item(param_name)
        except Exception:
            pass
    # Fallback: search all groups for the name.
    try:
        for grp in def_file.Groups:
            try:
                definition = grp.Definitions.get_Item(param_name)
                if definition:
                    return definition
            except Exception:
                continue
    except Exception:
        pass
    return None


def _collect_existing_bindings(doc):
    existing = {}
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        definition = it.Key
        binding = it.Current
        key = None
        try:
            key = definition.GUID
        except Exception:
            key = definition.Name
        existing[key] = (definition, binding)
    return existing


# ============================================================
# Main
# ============================================================
default_workbook = _pick_first_existing_path([DEFAULT_XLSX_PATH]) or DEFAULT_XLSX_PATH
default_shared = _pick_first_existing_path([DEFAULT_SHARED_PARAMETERS_PATH]) or DEFAULT_SHARED_PARAMETERS_PATH

selected_workbook, selected_shared = _show_import_dialog(default_workbook, default_shared)

if not selected_workbook and not selected_shared:
    _cancel_import("Project parameter import cancelled.")

if not selected_workbook:
    TaskDialog.Show("Error", "No Excel workbook path was entered.")
    raise Exception("Excel workbook not found")

if not _is_url(selected_workbook) and not os.path.exists(selected_workbook):
    TaskDialog.Show(
        "Error",
        "Excel workbook not found:\n{}".format(selected_workbook),
    )
    raise Exception("Excel workbook not found")

if not selected_shared:
    TaskDialog.Show("Error", "No Shared Parameters file path was entered.")
    raise Exception("Shared Parameters file not found")

if not _is_url(selected_shared) and not os.path.exists(selected_shared):
    TaskDialog.Show(
        "Error",
        "Shared Parameters file not found:\n{}".format(selected_shared),
    )
    raise Exception("Shared Parameters file not found")

_tmp_workbook_path = None
_tmp_shared_path = None
try:
    print("Project Parameter Import - Starting...")
    print("Started: {}".format(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    print("Excel Source: {}".format(selected_workbook))
    print("Worksheet: {}".format(DEFAULT_SHEET_NAME))
    print("Shared Parameters: {}".format(selected_shared))

    workbook_path = selected_workbook
    if _is_url(selected_workbook):
        try:
            print("Downloading Excel file from URL...")
            _tmp_workbook_path = _download_url_to_temp(selected_workbook, suffix=".xlsx")
            workbook_path = _tmp_workbook_path
            print("Downloaded to: {}".format(workbook_path))
        except Exception as dl_exc:
            TaskDialog.Show(
                "Error",
                "Failed to download Excel file from URL.\n{}\n\n"
                "Tip: use a direct download link. For SharePoint, try appending ?download=1 to the URL.".format(dl_exc),
            )
            raise Exception("URL download failed: {}".format(dl_exc))

    shared_path = selected_shared
    if _is_url(selected_shared):
        try:
            print("Downloading Shared Parameters file from URL...")
            _tmp_shared_path = _download_url_to_temp(selected_shared, suffix=".txt")
            shared_path = _tmp_shared_path
            print("Downloaded to: {}".format(shared_path))
        except Exception as dl_exc:
            TaskDialog.Show(
                "Error",
                "Failed to download Shared Parameters file from URL.\n{}\n\n"
                "Tip: use a direct download link. For SharePoint, try appending ?download=1 to the URL.".format(dl_exc),
            )
            raise Exception("URL download failed: {}".format(dl_exc))

    rows = list(_iter_excel_rows(workbook_path, worksheet_name=DEFAULT_SHEET_NAME))
    if not rows:
        raise Exception("No data found in worksheet")

    header_map = _build_header_map(rows[0])

    def _col_index(keys, fallback_idx):
        for k in keys:
            if k in header_map:
                return header_map[k]
        return fallback_idx

    col_param = _col_index(["parameter name", "parametername", "name"], 0)
    col_group_name = _col_index(["group name", "groupname"], 1)
    col_paratype_revit = _col_index(["paratyperevit", "parameter type revit"], 3)
    col_param_group = _col_index(["group", "parameter group"], 5)
    col_instance = _col_index(["instance parameter", "instance"], 6)
    col_category_api = _col_index(["category api", "categoryapi", "category_api"], 9)

    entries = []
    for row_idx, row in enumerate(rows, start=1):
        if row_idx < DATA_START_ROW:
            continue
        if not row or len(row) <= col_param:
            continue
        param_name = row[col_param].strip() if len(row) > col_param else ""
        if not param_name:
            break
        group_name = row[col_group_name].strip() if len(row) > col_group_name else ""
        paratype_revit = row[col_paratype_revit].strip() if len(row) > col_paratype_revit else ""
        param_group = row[col_param_group].strip() if len(row) > col_param_group else ""
        instance_raw = row[col_instance].strip() if len(row) > col_instance else ""
        category_api = row[col_category_api].strip() if len(row) > col_category_api else ""

        is_instance = str(instance_raw).strip().lower() in ["true", "1", "yes", "y"]

        entries.append({
            "param_name": param_name,
            "group_name": group_name,
            "paratype_revit": paratype_revit,
            "param_group": param_group,
            "is_instance": is_instance,
            "category_api": category_api,
        })

    if not entries:
        TaskDialog.Show("Warning", "No project parameter rows found in worksheet.")
        raise Exception("No project parameter rows found")

    grouped = {}
    for e in entries:
        key = (e["param_name"], e["group_name"], e["param_group"], e["is_instance"])
        grouped.setdefault(key, {"paratype_revit": e["paratype_revit"], "categories": []})
        grouped[key]["categories"].append(e["category_api"])

    app.SharedParametersFilename = shared_path
    def_file = app.OpenSharedParameterFile()
    if def_file is None:
        raise Exception("Revit could not open the Shared Parameters file.")

    existing = _collect_existing_bindings(doc)

    t = Transaction(doc, "Import Project Parameters")
    t.Start()

    added_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    try:
        for (param_name, group_name, param_group, is_instance), data in grouped.items():
            definition = _get_shared_definition(def_file, group_name, param_name)
            if definition is None:
                errors.append((param_name, "Definition not found in shared parameters file"))
                continue

            param_group_enum = _try_parse_param_group(param_group)
            if (_USE_BIP_GROUPS and param_group_enum == _INVALID_GROUP) or ((not _USE_BIP_GROUPS) and param_group_enum is None):
                errors.append((param_name, "Invalid parameter group: {}".format(param_group)))
                continue

            cat_set = app.Create.NewCategorySet()
            bad_categories = []
            for cat_name in data["categories"]:
                bic = _try_parse_built_in_category(cat_name)
                if bic is None:
                    bad_categories.append(cat_name)
                    continue
                try:
                    cat = doc.Settings.Categories.get_Item(bic)
                except Exception:
                    cat = None
                if cat is None:
                    bad_categories.append(cat_name)
                    continue
                cat_set.Insert(cat)

            if bad_categories:
                errors.append((param_name, "Invalid categories: {}".format(", ".join(bad_categories))))

            if cat_set.IsEmpty:
                errors.append((param_name, "No valid categories for binding"))
                continue

            key = None
            try:
                key = definition.GUID
            except Exception:
                key = definition.Name

            existing_entry = existing.get(key)
            if existing_entry:
                existing_def, existing_binding = existing_entry
                if is_instance and not isinstance(existing_binding, InstanceBinding):
                    errors.append((param_name, "Existing parameter is Type, requested Instance"))
                    skipped_count += 1
                    continue
                if (not is_instance) and not isinstance(existing_binding, TypeBinding):
                    errors.append((param_name, "Existing parameter is Instance, requested Type"))
                    skipped_count += 1
                    continue

                merged = app.Create.NewCategorySet()
                try:
                    for c in existing_binding.Categories:
                        merged.Insert(c)
                except Exception:
                    pass
                for c in cat_set:
                    merged.Insert(c)

                if isinstance(existing_binding, InstanceBinding):
                    new_binding = app.Create.NewInstanceBinding(merged)
                else:
                    new_binding = app.Create.NewTypeBinding(merged)

                doc.ParameterBindings.ReInsert(existing_def, new_binding, param_group_enum)
                updated_count += 1
            else:
                if is_instance:
                    binding = app.Create.NewInstanceBinding(cat_set)
                else:
                    binding = app.Create.NewTypeBinding(cat_set)

                inserted = doc.ParameterBindings.Insert(definition, binding, param_group_enum)
                if inserted:
                    added_count += 1
                else:
                    errors.append((param_name, "Insert failed (already exists or invalid binding)"))
                    skipped_count += 1

        t.Commit()
    except Exception as e:
        t.RollBack()
        raise

    print("Added: {}".format(added_count))
    print("Updated: {}".format(updated_count))
    print("Skipped: {}".format(skipped_count))
    if errors:
        print("Errors: {}".format(len(errors)))
        for name, msg in errors:
            print("  {}: {}".format(name, msg))

    summary_msg = "Import Complete!\n\nAdded: {}\nUpdated: {}\nSkipped: {}".format(
        added_count, updated_count, skipped_count
    )
    if errors:
        summary_msg += "\n\nErrors: {}".format(len(errors))
    TaskDialog.Show("Project Parameter Import", summary_msg)

except Exception as e:
    print("FATAL ERROR: {}".format(str(e)))
    print("Error Type: {}".format(type(e).__name__))
    try:
        print(traceback.format_exc())
    except Exception:
        pass
    TaskDialog.Show("Error", "Failed to process source file:\n{}".format(str(e)))
finally:
    if _tmp_workbook_path:
        try:
            os.remove(_tmp_workbook_path)
        except Exception:
            pass
    if _tmp_shared_path:
        try:
            os.remove(_tmp_shared_path)
        except Exception:
            pass
