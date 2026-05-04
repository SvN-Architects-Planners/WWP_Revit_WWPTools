# -*- coding: utf-8 -*-
#pylint: disable=unused-argument,too-many-lines
#pylint: disable=missing-function-docstring,missing-class-docstring
"""Print sheets in order from a sheet index.

Note:
When using the `Combine into one file` option
in Revit 2022 and earlier,
the tool adds non-printable character u'\u200e'
(Left-To-Right Mark) at the start of the sheet names
to push Revit's interenal printing engine to sort
the sheets correctly per the drawing index order.

Make sure your drawings indices consider this
when filtering for sheet numbers.

Shift-Click:
Shift-Clicking the tool will remove all
non-printable characters from the sheet numbers,
in case an error in the tool causes these characters
to remain.
"""
#pylint: disable=import-error,invalid-name,broad-except,superfluous-parens
import re
import os.path as op
import codecs
import csv
import unicodedata
import os, datetime, locale
from collections import namedtuple

from System import DateTime, Type, Activator, Array, Object, TimeSpan
from System.Runtime.InteropServices import Marshal
from System.Reflection import BindingFlags
from System.Threading import Thread

from pyrevit import HOST_APP
from pyrevit import framework
from pyrevit.framework import Windows, Drawing, ObjectModel, Forms, List
from pyrevit.framework import clr
from pyrevit import coreutils
from pyrevit import forms
from pyrevit import revit, DB
from pyrevit import script
from pyrevit.compat import get_elementid_value_func

try:
    from Microsoft.Win32 import OpenFileDialog, SaveFileDialog
except Exception:
    OpenFileDialog = Forms.OpenFileDialog
    SaveFileDialog = Forms.SaveFileDialog


get_elementid_value = get_elementid_value_func()

logger = script.get_logger()
config = script.get_config()


# Non Printable Char
NPC = u'\u200e'
INDEX_FORMAT = '{{:0{digits}}}'


def coerce_to_2d(values):
    if values is None:
        return None
    if hasattr(values, 'GetLowerBound'):
        try:
            # Already a 2D System.Array
            values.GetLowerBound(1)
            return values
        except Exception:
            # Excel can return a 1D array for degenerate ranges.
            # Normalize to a single-row 2D array.
            try:
                lb0 = values.GetLowerBound(0)
                ub0 = values.GetUpperBound(0)
            except Exception:
                arr = Array.CreateInstance(Object, 1, 1)
                arr[0, 0] = values
                return arr
            length = (ub0 - lb0) + 1
            if length < 1:
                return None
            arr = Array.CreateInstance(Object, 1, length)
            for idx in range(lb0, ub0 + 1):
                arr[0, idx - lb0] = values[idx]
            return arr
    arr = Array.CreateInstance(Object, 1, 1)
    arr[0, 0] = values
    return arr


def normalize_match_text(value):
    if value is None:
        return ''
    try:
        if isinstance(value, unicode):
            text = value
        elif isinstance(value, str):
            try:
                text = value.decode('utf-8')
            except Exception:
                text = value.decode('cp1252', 'ignore')
        else:
            text = unicode(value)
    except Exception:
        try:
            text = str(value)
        except Exception:
            text = ''
    text = text.strip().lstrip(u'\ufeff')
    try:
        text = unicodedata.normalize('NFC', text)
    except Exception:
        pass
    return text


EXPORT_ENCODING = 'utf_16_le'
if HOST_APP.is_newer_than(2020):
    EXPORT_ENCODING = 'utf_8'

IS_REVIT_2022_OR_NEWER = HOST_APP.is_newer_than(2021)


AvailableDoc = namedtuple('AvailableDoc', ['name', 'hash', 'linked'])

NamingFormatter = namedtuple('NamingFormatter', ['template', 'desc'])

SheetRevision = namedtuple('SheetRevision', ['number', 'desc', 'date', 'is_set'])
UNSET_REVISION = SheetRevision(number=None, desc=None, date=None, is_set=False)

TitleBlockPrintSettings = \
    namedtuple('TitleBlockPrintSettings', ['psettings', 'set_by_param'])

class PrintUtils:
    """Utility functions for printing and exporting sheets."""

    @staticmethod
    def get_doc():
        return revit.doc

    @staticmethod
    def get_dir():
        return os.path.join(os.path.expanduser("~"), "Desktop", "pyRevit Print Folder")

    @staticmethod
    def get_folder(task="_PDF"):
        dateStamp = datetime.datetime.today().strftime("%y%m%d")
        timeStamp = datetime.datetime.today().strftime("%H%M%S")
        return dateStamp + "_" + timeStamp + task

    @staticmethod
    def ensure_dir(dp):
        if not os.path.exists(dp):
            os.makedirs(dp)
        return dp

    @staticmethod
    def open_dir(dp):
        try:
            os.startfile(dp)
        except Exception:
            pass
        return dp

    @staticmethod
    def pdf_opts(hcb=True, hsb=True, hrp=True, hvt=True, mcl=True):
        opts = DB.PDFExportOptions()
        opts.HideCropBoundaries = hcb
        opts.HideScopeBoxes = hsb
        opts.HideReferencePlane = hrp
        opts.HideUnreferencedViewTags = hvt
        opts.MaskCoincidentLines = mcl
        opts.PaperFormat = DB.ExportPaperFormat.Default
        return opts

    @staticmethod
    def dwg_opts(sc=False, mv=True):
        opts = DB.DWGExportOptions()
        opts.SharedCoords = sc
        opts.MergedViews = mv
        return opts

    @staticmethod
    def export_sheet_pdf(dir_path, sheet, opt, doc, filename):
        pdf_doc_name = op.splitext(filename)[0]
        opt.FileName = pdf_doc_name
        export_sheet = List[DB.ElementId]()
        export_sheet.Add(sheet.Id)
        doc.Export(dir_path, export_sheet, opt)
        return True

    @staticmethod
    def export_sheet_dwg(dir_path, sheet, opt, doc, filename):
        base_name = op.splitext(filename)[0]
        dwg_doc_name = base_name + ".dwg"
        export_sheet = List[DB.ElementId]()
        export_sheet.Add(sheet.Id)
        doc.Export(dir_path, dwg_doc_name, export_sheet, opt)
        return True


class ComInterop(object):
    FLAGS = BindingFlags.Public | BindingFlags.Instance | BindingFlags.OptionalParamBinding

    @staticmethod
    def _to_object_array(values):
        values = values or ()
        arr = Array.CreateInstance(Object, len(values))
        for idx, value in enumerate(values):
            arr[idx] = value
        return arr

    @staticmethod
    def get(target, name, *args):
        if target is None:
            return None
        return target.GetType().InvokeMember(
            name,
            ComInterop.FLAGS | BindingFlags.GetProperty,
            None,
            target,
            ComInterop._to_object_array(args),
        )

    @staticmethod
    def set(target, name, value):
        if target is None:
            return
        target.GetType().InvokeMember(
            name,
            ComInterop.FLAGS | BindingFlags.SetProperty,
            None,
            target,
            ComInterop._to_object_array((value,)),
        )

    @staticmethod
    def call(target, name, *args):
        if target is None:
            return None
        return target.GetType().InvokeMember(
            name,
            ComInterop.FLAGS | BindingFlags.InvokeMethod,
            None,
            target,
            ComInterop._to_object_array(args),
        )

    @staticmethod
    def release(obj):
        try:
            if obj is not None and Marshal.IsComObject(obj):
                Marshal.FinalReleaseComObject(obj)
        except Exception:
            pass


class ExcelPrintRow(object):
    def __init__(self, file_name="", drawing_name="", drawing_number=""):
        self.PrintFileName = file_name
        self.DrawingName = drawing_name
        self.DrawingNumber = drawing_number


class ExcelDatabase(object):
    HEADER_FILE_NAME = "Printed File Name"
    HEADER_DRAWING_NAME = "Drawing Name"
    HEADER_DRAWING_NUMBER = "Drawing Number"

    @staticmethod
    def _path_exists_with_retry(path, retries=8, sleep_ms=150):
        candidate = (path or '').strip()
        if not candidate:
            return False
        for _ in range(retries):
            if op.exists(candidate):
                return True
            Thread.Sleep(sleep_ms)
        return op.exists(candidate)

    @staticmethod
    def _is_csv_path(path):
        try:
            return op.splitext(path or '')[1].lower() == '.csv'
        except Exception:
            return False

    @staticmethod
    def _to_text(value):
        if value is None:
            return ''
        try:
            if isinstance(value, unicode):
                return value
        except Exception:
            pass
        try:
            if isinstance(value, str):
                try:
                    return value.decode('utf-8')
                except Exception:
                    return value.decode('cp1252', 'ignore')
        except Exception:
            pass
        try:
            text = unicode(value)
        except Exception:
            text = str(value)
        return normalize_match_text(text)

    @staticmethod
    def _csv_cell(value):
        text = ExcelDatabase._to_text(value)
        try:
            return text.encode('utf-8')
        except Exception:
            return str(text)

    @staticmethod
    def _read_print_rows_csv(path):
        result = []
        if not op.exists(path):
            return result

        with open(path, 'rb') as csv_file:
            reader = csv.reader(csv_file)
            rows = list(reader)

        if not rows:
            return result

        headers = {}
        header_row = rows[0] or []
        for idx, val in enumerate(header_row):
            key = ExcelDatabase._to_text(val).strip().lstrip(u'\ufeff')
            if key and key not in headers:
                headers[key] = idx

        col_file_name = headers.get(ExcelDatabase.HEADER_FILE_NAME, 0)
        col_drawing_name = headers.get(ExcelDatabase.HEADER_DRAWING_NAME, 1)
        col_drawing_number = headers.get(ExcelDatabase.HEADER_DRAWING_NUMBER, 2)

        for row in rows[1:]:
            if not row:
                continue

            def safe_get(col_idx):
                if col_idx < 0 or col_idx >= len(row):
                    return ''
                return ExcelDatabase._to_text(row[col_idx]).strip()

            drawing_name = normalize_match_text(safe_get(col_drawing_name))
            if not drawing_name:
                continue

            file_name = safe_get(col_file_name)
            drawing_number = normalize_match_text(safe_get(col_drawing_number))
            result.append(ExcelPrintRow(file_name, drawing_name, drawing_number))

        return result

    @staticmethod
    def _generate_or_update_csv(path, sheets, name_map=None, number_map=None, force_update=False):
        existing_rows = ExcelDatabase._read_print_rows_csv(path) if op.exists(path) else []
        row_by_name = {}
        ordered_rows = []

        for row in existing_rows:
            key = (row.DrawingName or '').strip()
            if key and key not in row_by_name:
                row_by_name[key] = row
                ordered_rows.append(row)

        for view_sheet in sheets or []:
            drawing_name = normalize_match_text(getattr(view_sheet, 'Name', ''))
            drawing_number = normalize_match_text(getattr(view_sheet, 'SheetNumber', ''))
            if not drawing_name:
                continue
            default_file_name = "{0}_{1}".format(drawing_number, drawing_name)

            mapped_name = None
            if name_map and drawing_name in name_map:
                mapped_name = name_map.get(drawing_name)
            elif number_map and drawing_number in number_map:
                mapped_name = number_map.get(drawing_number)

            if isinstance(mapped_name, (list, tuple)):
                mapped_values = [normalize_match_text(x) for x in mapped_name if normalize_match_text(x)]
            else:
                mapped_text = normalize_match_text(mapped_name) if mapped_name else ''
                mapped_values = [mapped_text] if mapped_text else []

            if not mapped_values:
                mapped_values = [default_file_name]

            # Replace any existing rows for the same drawing name so that
            # CSV can carry one or multiple file-name variants per sheet.
            ordered_rows = [r for r in ordered_rows if normalize_match_text(r.DrawingName) != drawing_name]

            for mapped_value in mapped_values:
                ordered_rows.append(ExcelPrintRow(mapped_value, drawing_name, drawing_number))
            row_by_name[drawing_name] = ordered_rows[-1]

        with open(path, 'wb') as csv_file:
            csv_file.write(codecs.BOM_UTF8)
            writer = csv.writer(csv_file)
            writer.writerow([
                ExcelDatabase._csv_cell(ExcelDatabase.HEADER_FILE_NAME),
                ExcelDatabase._csv_cell(ExcelDatabase.HEADER_DRAWING_NAME),
                ExcelDatabase._csv_cell(ExcelDatabase.HEADER_DRAWING_NUMBER),
            ])
            for row in ordered_rows:
                writer.writerow([
                    ExcelDatabase._csv_cell(row.PrintFileName),
                    ExcelDatabase._csv_cell(row.DrawingName),
                    ExcelDatabase._csv_cell(row.DrawingNumber),
                ])

        return op.abspath(path)

    @staticmethod
    def generate_or_update(path, sheets, name_map=None, number_map=None, force_update=False):
        if ExcelDatabase._is_csv_path(path):
            return ExcelDatabase._generate_or_update_csv(
                path, sheets, name_map=name_map, number_map=number_map, force_update=force_update)

        excel = None
        workbooks = None
        workbook = None
        worksheets = None
        sheet = None
        used_range = None
        cells = None
        saved_path = path

        try:
            excel_type = Type.GetTypeFromProgID("Excel.Application")
            if excel_type is None:
                raise Exception("Excel is not installed.")

            excel = Activator.CreateInstance(excel_type)
            ComInterop.set(excel, "Visible", False)
            ComInterop.set(excel, "DisplayAlerts", False)

            workbooks = ComInterop.get(excel, "Workbooks")
            if op.exists(path):
                workbook = ComInterop.call(workbooks, "Open", path)
            else:
                workbook = ComInterop.call(workbooks, "Add")

            worksheets = ComInterop.get(workbook, "Worksheets")
            sheet = ComInterop.get(worksheets, "Item", 1)
            used_range = ComInterop.get(sheet, "UsedRange")
            cells = ComInterop.get(sheet, "Cells")

            col_file_name = 1
            col_drawing_name = 2
            col_drawing_number = 3
            ExcelDatabase.ensure_headers(cells, col_file_name, col_drawing_name, col_drawing_number)

            values = coerce_to_2d(ComInterop.get(used_range, "Value2"))
            row_by_drawing_name = ExcelDatabase.read_existing_rows(values, col_drawing_name) if values else {}

            row_start = values.GetLowerBound(0) if values else 1
            row_end = values.GetUpperBound(0) if values else 1
            last_row = (row_end - row_start) + 2
            if last_row < 2:
                last_row = 2

            for view_sheet in sheets:
                drawing_name = view_sheet.Name
                drawing_number = view_sheet.SheetNumber
                default_file_name = "{0}_{1}".format(drawing_number, drawing_name)

                mapped_name = None
                if name_map and drawing_name in name_map:
                    mapped_name = name_map.get(drawing_name)
                elif number_map and drawing_number in number_map:
                    mapped_name = number_map.get(drawing_number)

                if drawing_name in row_by_drawing_name:
                    row_index = row_by_drawing_name[drawing_name]
                    current_file_name = ExcelDatabase.get_cell_value(cells, row_index, col_file_name)
                    if mapped_name:
                        ExcelDatabase.set_cell_value(cells, row_index, col_file_name, mapped_name)
                    elif force_update and default_file_name:
                        ExcelDatabase.set_cell_value(cells, row_index, col_file_name, default_file_name)
                    elif not current_file_name or not str(current_file_name).strip():
                        ExcelDatabase.set_cell_value(cells, row_index, col_file_name, default_file_name)

                    ExcelDatabase.set_cell_value(cells, row_index, col_drawing_name, drawing_name)
                    ExcelDatabase.set_cell_value(cells, row_index, col_drawing_number, drawing_number)
                else:
                    ExcelDatabase.set_cell_value(cells, last_row, col_file_name, mapped_name or default_file_name)
                    ExcelDatabase.set_cell_value(cells, last_row, col_drawing_name, drawing_name)
                    ExcelDatabase.set_cell_value(cells, last_row, col_drawing_number, drawing_number)
                    row_by_drawing_name[drawing_name] = last_row
                    last_row += 1

            if op.exists(path):
                ComInterop.call(workbook, "Save")
            else:
                saved = False
                try:
                    ComInterop.call(workbook, "SaveAs", path)
                    saved = ExcelDatabase._path_exists_with_retry(path)
                except Exception:
                    saved = False

                # Excel can silently ignore SaveAs in some COM contexts unless
                # explicit file format is provided or copy-save is used.
                if not saved:
                    try:
                        ComInterop.call(workbook, "SaveAs", path, 51)  # xlOpenXMLWorkbook
                        saved = ExcelDatabase._path_exists_with_retry(path)
                    except Exception:
                        saved = False

                if not saved:
                    try:
                        ComInterop.call(workbook, "SaveCopyAs", path)
                        saved = ExcelDatabase._path_exists_with_retry(path)
                    except Exception:
                        saved = False

            if not ExcelDatabase._path_exists_with_retry(path):
                workbook_fullname = ComInterop.get(workbook, "FullName")
                if workbook_fullname:
                    workbook_fullname = op.abspath(str(workbook_fullname))
                    if ExcelDatabase._path_exists_with_retry(workbook_fullname):
                        saved_path = workbook_fullname
            if not ExcelDatabase._path_exists_with_retry(saved_path):
                raise Exception("Excel save completed but no file was created on disk.")
            return saved_path
        finally:
            if workbook is not None:
                ComInterop.call(workbook, "Close", False)
            if excel is not None:
                ComInterop.call(excel, "Quit")

            ComInterop.release(cells)
            ComInterop.release(used_range)
            ComInterop.release(sheet)
            ComInterop.release(worksheets)
            ComInterop.release(workbook)
            ComInterop.release(workbooks)
            ComInterop.release(excel)

    @staticmethod
    def read_print_rows(path):
        if ExcelDatabase._is_csv_path(path):
            return ExcelDatabase._read_print_rows_csv(path)

        result = []

        excel = None
        workbooks = None
        workbook = None
        worksheets = None
        sheet = None
        used_range = None

        try:
            excel_type = Type.GetTypeFromProgID("Excel.Application")
            if excel_type is None:
                raise Exception("Excel is not installed.")

            excel = Activator.CreateInstance(excel_type)
            ComInterop.set(excel, "Visible", False)

            workbooks = ComInterop.get(excel, "Workbooks")
            workbook = ComInterop.call(workbooks, "Open", path)
            worksheets = ComInterop.get(workbook, "Worksheets")
            sheet = ComInterop.get(worksheets, "Item", 1)
            used_range = ComInterop.get(sheet, "UsedRange")
            return ExcelDatabase.read_print_rows_from_used_range(used_range)
        finally:
            if workbook is not None:
                ComInterop.call(workbook, "Close", False)
            if excel is not None:
                ComInterop.call(excel, "Quit")

            ComInterop.release(used_range)
            ComInterop.release(sheet)
            ComInterop.release(worksheets)
            ComInterop.release(workbook)
            ComInterop.release(workbooks)
            ComInterop.release(excel)

    @staticmethod
    def read_print_rows_from_used_range(used_range):
        result = []
        if used_range is None:
            return result

        cells = None
        rows_obj = None
        cols_obj = None
        try:
            cells = ComInterop.get(used_range, "Cells")
            rows_obj = ComInterop.get(used_range, "Rows")
            cols_obj = ComInterop.get(used_range, "Columns")

            row_count = int(ComInterop.get(rows_obj, "Count") or 0)
            col_count = int(ComInterop.get(cols_obj, "Count") or 0)
            if row_count < 1 or col_count < 1:
                return result

            headers = {}
            for col in range(1, col_count + 1):
                header_val = ExcelDatabase.get_cell_value(cells, 1, col)
                if header_val is None:
                    continue
                header = str(header_val).strip()
                if not header:
                    continue
                if header not in headers:
                    headers[header] = col

            col_file_name = headers.get(ExcelDatabase.HEADER_FILE_NAME, 1)
            col_drawing_name = headers.get(ExcelDatabase.HEADER_DRAWING_NAME, 2)
            col_drawing_number = headers.get(ExcelDatabase.HEADER_DRAWING_NUMBER, 3)

            if col_drawing_name < 1 or col_drawing_name > col_count:
                return result

            for row in range(2, row_count + 1):
                drawing_name_val = ExcelDatabase.get_cell_value(cells, row, col_drawing_name)
                if drawing_name_val is None:
                    continue

                drawing_name = str(drawing_name_val).strip()
                if not drawing_name:
                    continue

                file_name = ""
                if col_file_name >= 1 and col_file_name <= col_count:
                    file_name_val = ExcelDatabase.get_cell_value(cells, row, col_file_name)
                    if file_name_val is not None:
                        file_name = str(file_name_val).strip()

                drawing_number = ""
                if col_drawing_number >= 1 and col_drawing_number <= col_count:
                    drawing_number_val = ExcelDatabase.get_cell_value(cells, row, col_drawing_number)
                    if drawing_number_val is not None:
                        drawing_number = str(drawing_number_val).strip()

                result.append(ExcelPrintRow(file_name, drawing_name, drawing_number))
            return result
        finally:
            ComInterop.release(cols_obj)
            ComInterop.release(rows_obj)
            ComInterop.release(cells)

    @staticmethod
    def read_existing_rows(values, col_drawing_name):
        rows = {}
        if values is None:
            return rows
        row_start = values.GetLowerBound(0)
        row_end = values.GetUpperBound(0)
        col_start = values.GetLowerBound(1)
        col_end = values.GetUpperBound(1)
        col_index = col_drawing_name - 1 + col_start
        if col_index < col_start or col_index > col_end:
            return rows

        for row in range(row_start + 1, row_end + 1):
            name_val = values[row, col_index]
            if name_val is None:
                continue
            name = str(name_val).strip()
            if not name:
                continue
            if name not in rows:
                rows[name] = (row - row_start) + 1
        return rows

    @staticmethod
    def ensure_headers(cells, col_file_name, col_drawing_name, col_drawing_number):
        ExcelDatabase.set_cell_value(cells, 1, col_file_name, ExcelDatabase.HEADER_FILE_NAME)
        ExcelDatabase.set_cell_value(cells, 1, col_drawing_name, ExcelDatabase.HEADER_DRAWING_NAME)
        ExcelDatabase.set_cell_value(cells, 1, col_drawing_number, ExcelDatabase.HEADER_DRAWING_NUMBER)

    @staticmethod
    def get_cell_value(cells, row, col):
        cell = None
        try:
            cell = ComInterop.get(cells, "Item", row, col)
            return ComInterop.get(cell, "Value2")
        except Exception:
            return None
        finally:
            ComInterop.release(cell)

    @staticmethod
    def set_cell_value(cells, row, col, value):
        cell = None
        try:
            cell = ComInterop.get(cells, "Item", row, col)
            ComInterop.set(cell, "Value2", value)
        finally:
            ComInterop.release(cell)


class FolderPicker(object):
    @staticmethod
    def pick_folder(initial_path=None):
        picked = FolderPicker._try_common_dialog(initial_path)
        if picked:
            return picked
        try:
            return forms.pick_folder()
        except Exception:
            return None

    @staticmethod
    def _try_common_dialog(initial_path):
        try:
            dialog_type = Type.GetType(
                "Microsoft.WindowsAPICodePack.Dialogs.CommonOpenFileDialog, Microsoft.WindowsAPICodePack"
            )
            if dialog_type is None:
                return None

            dialog = Activator.CreateInstance(dialog_type)
            dialog_type.GetProperty("IsFolderPicker").SetValue(dialog, True, None)
            dialog_type.GetProperty("Multiselect").SetValue(dialog, False, None)
            dialog_type.GetProperty("Title").SetValue(dialog, "Select Output Folder", None)
            if initial_path:
                prop = dialog_type.GetProperty("InitialDirectory")
                if prop is not None:
                    prop.SetValue(dialog, initial_path, None)

            show_dialog = dialog_type.GetMethod("ShowDialog")
            result = show_dialog.Invoke(dialog, None) if show_dialog else None
            ok = False
            if result is not None:
                try:
                    ok = int(result) == 1
                except Exception:
                    text = str(result)
                    ok = text.lower() in ("ok", "1")

            if ok:
                return dialog_type.GetProperty("FileName").GetValue(dialog, None)
        except Exception:
            return None
        return None
    
class NamingFormat(forms.Reactive):
    """Print File Naming Format"""
    def __init__(self, name, template, builtin=False):
        self._name = name
        self._template = self.verify_template(template)
        self.builtin = builtin

    @staticmethod
    def verify_template(value):
        """Verify template is valid"""
        if not value.lower().endswith('.pdf'):
            value += '.pdf'
        return value

    @forms.reactive
    def name(self):
        """Format name"""
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @forms.reactive
    def template(self):
        """Format template string"""
        return self._template

    @template.setter
    def template(self, value):
        self._template = self.verify_template(value)


class ViewSheetListItem(forms.Reactive):
    """Revit Sheet show in Print Window"""

    def __init__(self, view_sheet, view_tblock,
                 print_settings=None, rev_settings=None):
        self._sheet = view_sheet
        self._tblock = view_tblock
        if self._tblock:
            self._tblock_type = \
                view_sheet.Document.GetElement(view_tblock.GetTypeId())
        else:
            self._tblock_type = None
        self.name = self._sheet.Name
        self.number = self._sheet.SheetNumber if hasattr(self._sheet, 'SheetNumber') else ''
        self.issue_date = \
            self._sheet.Parameter[
                DB.BuiltInParameter.SHEET_ISSUE_DATE].AsString() if self._sheet.Parameter[
                DB.BuiltInParameter.SHEET_ISSUE_DATE] else ''
        self.printable = self._sheet.CanBePrinted
        self.revision_date_sortable = ""
        self._print_index = 0
        self._print_filename = ''

        self._tblock_psettings = print_settings
        self._print_settings = self._tblock_psettings.psettings
        self.all_print_settings = self._tblock_psettings.psettings
        if self.all_print_settings:
            self._print_settings = self.all_print_settings[0]
        self.read_only = self._tblock_psettings.set_by_param

        per_sheet_revisions = \
            rev_settings.RevisionNumbering == DB.RevisionNumbering.PerSheet \
            if rev_settings else False
        cur_rev = revit.query.get_current_sheet_revision(self._sheet) if hasattr(self._sheet, 'GetCurrentRevision') else ''
        self.revision = UNSET_REVISION
        if cur_rev:
            on_sheet = self._sheet if per_sheet_revisions else None
            self.revision = SheetRevision(
                number=revit.query.get_rev_number(cur_rev, sheet=on_sheet),
                desc=cur_rev.Description,
                date=cur_rev.RevisionDate,
                is_set=True
            )

        

    @property
    def revit_sheet(self):
        """Revit sheet instance"""
        return self._sheet

    @property
    def revit_tblock(self):
        """Revit titleblock instance"""
        return self._tblock

    @property
    def revit_tblock_type(self):
        """Revit titleblock type"""
        return self._tblock_type

    @forms.reactive
    def print_settings(self):
        """Sheet pring settings"""
        return self._print_settings

    @print_settings.setter
    def print_settings(self, value):
        self._print_settings = value

    @forms.reactive
    def print_index(self):
        """Sheet print index"""
        return self._print_index

    @print_index.setter
    def print_index(self, value):
        self._print_index = value

    @forms.reactive
    def print_filename(self):
        """Sheet print output filename"""
        return self._print_filename

    @print_filename.setter
    def print_filename(self, value):
        self._print_filename = \
            coreutils.cleanup_filename(value, windows_safe=True)


class PrintSettingListItem(forms.TemplateListItem):
    """Print Setting shown in Print Window"""

    def __init__(self, print_settings=None):
        super(PrintSettingListItem, self).__init__(print_settings)
        self.is_compatible = \
            True if isinstance(self.item, DB.InSessionPrintSetting) \
                else False

    @property
    def name(self):
        if isinstance(self.item, DB.InSessionPrintSetting):
            return "<In Session>"
        else:
            return self.item.Name

    @property
    def print_settings(self):
        return self.item

    @property
    def print_params(self):
        if self.print_settings:
            return self.print_settings.PrintParameters

    @property
    def paper_size(self):
        try:
            if self.print_params:
                return self.print_params.PaperSize
        except Exception:
            pass

    @property
    def allows_variable_paper(self):
        return False

    @property
    def is_user_defined(self):
        return not self.name.startswith('<')


class VariablePaperPrintSettingListItem(PrintSettingListItem):
    def __init__(self):
        PrintSettingListItem.__init__(self, None)
        # always compatible
        self.is_compatible = True

    @property
    def name(self):
        return "<Variable Paper Size>"

    @property
    def allows_variable_paper(self):
        return True


class EditNamingFormatsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name, start_with=None, doc=None):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._drop_pos = 0
        self._starting_item = start_with
        self._saved = False
        self._doc = doc

        self.reset_naming_formats()
        self.reset_formatters()

    @staticmethod
    def _get_project_param_names(doc):
        names = []
        if not doc:
            return names
        try:
            params = doc.ProjectInformation.Parameters
        except Exception:
            params = None
        if not params:
            return names
        try:
            for p in params:
                try:
                    name = p.Definition.Name if p and p.Definition else None
                except Exception:
                    name = None
                if name and name not in names:
                    names.append(name)
        except Exception:
            pass
        try:
            names.sort(key=lambda x: x.lower())
        except Exception:
            pass
        return names

    @staticmethod
    def get_default_formatters(doc=None):
        formatters = [
            NamingFormatter(
                template='{index}',
                desc='Print Index Number e.g. "0001"'
            ),
            NamingFormatter(
                template='{number}',
                desc='Sheet Number e.g. "A1.00"'
            ),
            NamingFormatter(
                template='{name}',
                desc='Sheet Name e.g. "1ST FLOOR PLAN"'
            ),
            NamingFormatter(
                template='{name_dash}',
                desc='Sheet Name (with - for space) e.g. "1ST-FLOOR-PLAN"'
            ),
            NamingFormatter(
                template='{name_underline}',
                desc='Sheet Name (with _ for space) e.g. "1ST_FLOOR_PLAN"'
            ),
            NamingFormatter(
                template='{current_date}',
                desc='Today''s Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{issue_date}',
                desc='Sheet Issue Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{rev_number}',
                desc='Revision Number e.g. "01"'
            ),
            NamingFormatter(
                template='{rev_desc}',
                desc='Revision Description e.g. "ASI01"'
            ),
            NamingFormatter(
                template='{rev_date}',
                desc='Revision Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{proj_name}',
                desc='Project Name e.g. "MY_PROJECT"'
            ),
            NamingFormatter(
                template='{proj_number}',
                desc='Project Number e.g. "PR2019.12"'
            ),
            NamingFormatter(
                template='{proj_building_name}',
                desc='Project Building Name e.g. "BLDG01"'
            ),
            NamingFormatter(
                template='{proj_issue_date}',
                desc='Project Issue Date e.g. "2019-10-12"'
            ),
            NamingFormatter(
                template='{proj_org_name}',
                desc='Project Organization Name e.g. "MYCOMP"'
            ),
            NamingFormatter(
                template='{proj_status}',
                desc='Project Status e.g. "CD100"'
            ),
            NamingFormatter(
                template='{username}',
                desc='Active User e.g. "eirannejad"'
            ),
            NamingFormatter(
                template='{revit_version}',
                desc='Active Revit Version e.g. "2019"'
            ),
            NamingFormatter(
                template='{excel_name}',
                desc='Excel Drawing Name (Column B)'
            ),
            NamingFormatter(
                template='{excel_number}',
                desc='Excel Drawing Number (Column C)'
            ),
            NamingFormatter(
                template='{sheet_param:PARAM_NAME}',
                desc='Value of Given Sheet Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{tblock_param:PARAM_NAME}',
                desc='Value of Given TitleBlock Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{proj_param:PARAM_NAME}',
                desc='Value of Given Project Information Parameter e.g. '
                     'Replace PARAM_NAME with target parameter name'
            ),
            NamingFormatter(
                template='{glob_param:PARAM_NAME}',
                desc='Value of Given Global Parameter. '
                     'Replace PARAM_NAME with target parameter name'
            ),
        ]

        proj_params = EditNamingFormatsWindow._get_project_param_names(doc)
        for pname in proj_params:
            formatters.append(
                NamingFormatter(
                    template='{proj_param:%s}' % pname,
                    desc='Project Parameter: %s' % pname
                )
            )

        return formatters

    @staticmethod
    def get_default_naming_formats():
        return [
            NamingFormat(
                name='0001 A1.00 1ST FLOOR PLAN.pdf',
                template='{index} {number} {name}.pdf',
                builtin=True
            ),
            NamingFormat(
                name='0001_A1.00_1ST FLOOR PLAN.pdf',
                template='{index}_{number}_{name}.pdf',
                builtin=True
            ),
            NamingFormat(
                name='0001-A1.00-1ST FLOOR PLAN.pdf',
                template='{index}-{number}-{name}.pdf',
                builtin=True
            ),
        ]

    @staticmethod
    def get_naming_formats():
        naming_formats = EditNamingFormatsWindow.get_default_naming_formats()
        naming_formats_dict = config.get_option('namingformats', {})
        for name, template in naming_formats_dict.items():
            naming_formats.append(NamingFormat(name=name, template=template))
        return naming_formats

    @staticmethod
    def set_naming_formats(naming_formats):
        naming_formats_dict = {
            x.name:x.template for x in naming_formats if not x.builtin
        }
        config.namingformats = naming_formats_dict
        script.save_config()

    @property
    def naming_formats(self):
        return self.formats_lb.ItemsSource

    @property
    def selected_naming_format(self):
        return self.formats_lb.SelectedItem

    @selected_naming_format.setter
    def selected_naming_format(self, value):
        self.formats_lb.SelectedItem = value
        self.namingformat_edit.DataContext = value

    def reset_formatters(self):
        self.formatters_wp.ItemsSource = \
            EditNamingFormatsWindow.get_default_formatters(self._doc)

    def reset_naming_formats(self):
        self.formats_lb.ItemsSource = \
                ObjectModel.ObservableCollection[object](
                    EditNamingFormatsWindow.get_naming_formats()
                )
        if isinstance(self._starting_item, NamingFormat):
            for item in self.formats_lb.ItemsSource:
                if item.name == self._starting_item.name:
                    self.selected_naming_format = item
                    break

    # https://www.wpftutorial.net/DragAndDrop.html
    def start_drag(self, sender, args):
        name_formatter = args.OriginalSource.DataContext
        Windows.DragDrop.DoDragDrop(
            self.formatters_wp,
            Windows.DataObject("name_formatter", name_formatter),
            Windows.DragDropEffects.Copy
            )

    # https://social.msdn.microsoft.com/Forums/vstudio/en-US/941f6bf2-a321-459e-85c9-501ec1e13204/how-do-you-get-a-drag-and-drop-event-for-a-wpf-textbox-hosted-in-a-windows-form
    def preview_drag(self, sender, args):
        mouse_pos = Forms.Cursor.Position
        mouse_po_pt = Windows.Point(mouse_pos.X, mouse_pos.Y)
        self._drop_pos = \
            self.template_tb.GetCharacterIndexFromPoint(
                point=self.template_tb.PointFromScreen(mouse_po_pt),
                snapToText=True
                )
        self.template_tb.SelectionStart = self._drop_pos
        self.template_tb.SelectionLength = 0
        self.template_tb.Focus()
        args.Effects = Windows.DragDropEffects.Copy
        args.Handled = True

    def stop_drag(self, sender, args):
        name_formatter = args.Data.GetData("name_formatter")
        if name_formatter:
            new_template = \
                str(self.template_tb.Text)[:self._drop_pos] \
                + name_formatter.template \
                + str(self.template_tb.Text)[self._drop_pos:]
            self.template_tb.Text = new_template
            self.template_tb.Focus()

    def namingformat_changed(self, sender, args):
        naming_format = self.selected_naming_format
        self.namingformat_edit.DataContext = naming_format

    def duplicate_namingformat(self, sender, args):
        naming_format = self.selected_naming_format
        new_naming_format = NamingFormat(
            name='<unnamed>',
            template=naming_format.template
            )
        self.naming_formats.Add(new_naming_format)
        self.selected_naming_format = new_naming_format

    def delete_namingformat(self, sender, args):
        naming_format = self.selected_naming_format
        if naming_format.builtin:
            return
        item_index = self.naming_formats.IndexOf(naming_format)
        self.naming_formats.Remove(naming_format)
        next_index = min([item_index, self.naming_formats.Count-1])
        self.selected_naming_format = self.naming_formats[next_index]

    def save_formats(self, sender, args):
        EditNamingFormatsWindow.set_naming_formats(self.naming_formats)
        self._saved = True
        self.Close()

    def cancelled(self, sender, args):
        if not self._saved:
            self.reset_naming_formats()

    def show_dialog(self):
        self.ShowDialog()

class SheetSetList(object):
    """List of sheets from a named Revit Sheet Set."""
    def __init__(self, view_sheetset):
        self.doc = view_sheetset.Document
        self.name = view_sheetset.Name
        self.sheetset = view_sheetset

    def get_sheets(self, doc):
        if doc == self.doc:
            return list(self.sheetset.Views)
        return []

class ScheduleSheetList(object):
    def __init__(self, view_shedule):
        self.doc = view_shedule.Document
        self.name = view_shedule.Name
        self.schedule = view_shedule

    def get_sheets(self, doc):
        return self._get_ordered_schedule_sheets(doc)

    def _get_schedule_text_data(self, view_shedule):
        schedule_data_file = \
            script.get_instance_data_file(str(get_elementid_value(view_shedule.Id)))
        vseop = DB.ViewScheduleExportOptions()
        vseop.TextQualifier = coreutils.get_enum_none(DB.ExportTextQualifier)
        view_shedule.Export(op.dirname(schedule_data_file),
                             op.basename(schedule_data_file),
                             vseop)

        sched_data = []
        try:
            with codecs.open(schedule_data_file, 'r', EXPORT_ENCODING) \
                    as sched_data_file:
                return [x.strip() for x in sched_data_file.readlines()]
        except Exception as open_err:
            logger.error('Error opening sheet index export: %s | %s',
                         schedule_data_file, open_err)
            return sched_data

    def _order_sheets_by_schedule_data(self, view_shedule, sheet_list):
        sched_data = self._get_schedule_text_data(view_shedule)

        if not sched_data:
            return sheet_list

        ordered_sheets_dict = {}
        for sheet in sheet_list:
            logger.debug('finding index for: %s', sheet.SheetNumber)
            for line_no, data_line in enumerate(sched_data):
                match_pattern = r'(^|.*\t){}(\t.*|$)'.format(sheet.SheetNumber)
                matches_sheet = re.match(match_pattern, data_line)
                logger.debug('match: %s', matches_sheet)
                try:
                    if matches_sheet:
                        ordered_sheets_dict[line_no] = sheet
                        break
                    if not sheet.CanBePrinted:
                        logger.debug('Sheet %s is not printable.',
                                     sheet.SheetNumber)
                except Exception:
                    continue

        sorted_keys = sorted(ordered_sheets_dict.keys())
        return [ordered_sheets_dict[x] for x in sorted_keys]

    def _get_ordered_schedule_sheets(self, doc):
        if doc == self.doc:
            sheets = DB.FilteredElementCollector(self.doc,
                                                 self.schedule.Id)\
                    .OfClass(framework.get_type(DB.ViewSheet))\
                    .WhereElementIsNotElementType()\
                    .ToElements()

            return self._order_sheets_by_schedule_data(
                self.schedule,
                sheets
                )
        return []


class AllSheetsList(object):
    @property
    def name(self):
        return "<All Sheets>"

    def get_sheets(self, doc):
        return DB.FilteredElementCollector(doc)\
                 .OfClass(framework.get_type(DB.ViewSheet))\
                 .WhereElementIsNotElementType()\
                 .ToElements()


class UnlistedSheetsList(object):
    @property
    def name(self):
        return "<Unlisted Sheets>"

    def get_sheets(self, doc):
        scheduled_param_id = DB.ElementId(DB.BuiltInParameter.SHEET_SCHEDULED)
        param_prov = DB.ParameterValueProvider(scheduled_param_id)
        param_equality = DB.FilterNumericEquals()
        value_rule = DB.FilterIntegerRule(param_prov, param_equality, 0)
        param_filter = DB.ElementParameterFilter(value_rule)
        return DB.FilteredElementCollector(doc)\
                 .OfClass(framework.get_type(DB.ViewSheet))\
                 .WherePasses(param_filter) \
                 .WhereElementIsNotElementType()\
                 .ToElements()



class PrintSheetsWindow(forms.WPFWindow):
    def __init__(self, xaml_file_name):
        forms.WPFWindow.__init__(self, xaml_file_name)

        self._init_psettings = None
        self._scheduled_sheets = []
        self._excel_rows_by_name = {}
        self._excel_rows_by_number = {}
        self._excel_path = ''
        self._scheduler = None
        self._scheduled_execution = False
        self._suppress_csv_popups = False

        self.project_info = revit.query.get_project_info(doc=revit.doc)
        self.sheet_cat_id = \
            revit.query.get_category(DB.BuiltInCategory.OST_Sheets).Id

        self._setup_docs_list()
        self._setup_naming_formats()

        # defaults for new controls
        try:
            if hasattr(self, 'schedule_date') and self.schedule_date:
                self.schedule_date.SelectedDate = DateTime.Today
            if hasattr(self, 'schedule_time_tb') and self.schedule_time_tb:
                self.schedule_time_tb.Text = DateTime.Now.AddHours(1).ToString("HH:mm")
            if hasattr(self, 'schedule_status_tb') and self.schedule_status_tb:
                self.schedule_status_tb.Text = "No schedule"
        except Exception:
            pass

        try:
            if hasattr(self, 'output_dir_tb') and self.output_dir_tb:
                if not self.output_dir_tb.Text:
                    self.output_dir_tb.Text = PrintUtils.get_dir()
        except Exception:
            pass

        self._scheduler = PrintScheduler(self)
        try:
            self.Closing += self.window_closing
        except Exception:
            pass

        try:
            if hasattr(self, 'excel_writeback_cb') and self.excel_writeback_cb:
                self.excel_writeback_cb.IsChecked = True
        except Exception:
            pass

    # doc and schedule
    @property
    def selected_doc(self):
        selected_doc = self.documents_cb.SelectedItem
        for opened_doc in revit.docs:
            if opened_doc.GetHashCode() == selected_doc.hash:
                return opened_doc

    @property
    def selected_sheetlist(self):
        return self.schedules_cb.SelectedItem

    # misc
    @property
    def has_errors(self):
        return self.errormsg_tb.Text != ''

    # ordering configs
    @property
    def reverse_print(self):
        return self.reverse_cb.IsChecked

    @property
    def combine_print(self):
        return self.combine_cb.IsChecked

    @property
    def export_pdf_enabled(self):
        try:
            return self.export_pdf.IsChecked
        except Exception:
            return True

    @property
    def export_dwg_enabled(self):
        try:
            return self.export_dwg.IsChecked
        except Exception:
            return False

    @property
    def show_placeholders(self):
        return self.placeholder_cb.IsChecked

    @property
    def index_digits(self):
        return int(self.index_slider.Value)

    @property
    def index_start(self):
        return int(self.indexstart_tb.Text or 0)

    @property
    def include_placeholders(self):
        return self.indexspace_cb.IsChecked

    # print settings
    @property
    def selected_naming_format(self):
        return self.namingformat_cb.SelectedItem

    @property
    def selected_printer(self):
        return self.printers_cb.SelectedItem

    @property
    def selected_print_setting(self):
        return self.printsettings_cb.SelectedItem

    @property
    def has_print_settings(self):
        # self.selected_print_setting implements __nonzero__
        # manually check None-ness
        return self.selected_print_setting is not None

    @property
    def print_settings(self):
        return self.printsettings_cb.ItemsSource

    # sheet list
    @property
    def sheet_list(self):
        return self.sheets_lb.ItemsSource

    @sheet_list.setter
    def sheet_list(self, value):
        self.sheets_lb.ItemsSource = value

    @property
    def selected_sheets(self):
        return self.sheets_lb.SelectedItems

    @property
    def printable_sheets(self):
        return [x for x in self.sheet_list if x.printable]

    @property
    def selected_printable_sheets(self):
        return [x for x in self.selected_sheets if x.printable]

    # private utils
    def _is_sheet_index(self, schedule_view):
        return self.sheet_cat_id == schedule_view.Definition.CategoryId \
               and not schedule_view.IsTemplate

    def _get_sheet_index_list(self):
        schedules = DB.FilteredElementCollector(self.selected_doc)\
                      .OfClass(framework.get_type(DB.ViewSchedule))\
                      .WhereElementIsNotElementType()\
                      .ToElements()

        return [
            ScheduleSheetList(s) for s in schedules
            if self._is_sheet_index(s)
            ]

    def _get_printmanager(self):
        try:
            return self.selected_doc.PrintManager
        except Exception as printerr:
            logger.critical('Error getting printer manager from document. '
                            'Most probably there is not a printer defined '
                            'on your system. | %s', printerr)
            script.exit()

    def _setup_docs_list(self):
        if not revit.doc.IsFamilyDocument:
            docs = [AvailableDoc(name=revit.doc.Title,
                                 hash=revit.doc.GetHashCode(),
                                 linked=False)]
            docs.extend([
                AvailableDoc(name=x.Title, hash=x.GetHashCode(), linked=True)
                for x in revit.query.get_all_linkeddocs(doc=revit.doc)
            ])
            self.documents_cb.ItemsSource = docs
            self.documents_cb.SelectedIndex = 0

    def _setup_naming_formats(self):
        self.namingformat_cb.ItemsSource = \
            EditNamingFormatsWindow.get_naming_formats()
        self.namingformat_cb.SelectedIndex = 0

    def _setup_printers(self):
        printers = list(Drawing.Printing.PrinterSettings.InstalledPrinters)
        self.printers_cb.ItemsSource = printers
        print_mgr = self._get_printmanager()
        self.printers_cb.SelectedItem = print_mgr.PrinterName

    def _get_psetting_items(self, doc,
                            psettings=None, include_varsettings=False):
        if include_varsettings:
            psetting_items = [VariablePaperPrintSettingListItem()]
        else:
            psetting_items = []

        psettings = psettings or revit.query.get_all_print_settings(doc=doc)
        psetting_items.extend([PrintSettingListItem(x) for x in psettings])

        print_mgr = self._get_printmanager()
        compatible_sizes = {x.Name for x in print_mgr.PaperSizes}
        for psetting_item in psetting_items:
            if isinstance(psetting_item, PrintSettingListItem):
                if psetting_item.paper_size \
                        and psetting_item.paper_size.Name in compatible_sizes:
                    psetting_item.is_compatible = True
        return psetting_items

    def _setup_print_settings(self):
        psetting_items = \
            self._get_psetting_items(
                doc=self.selected_doc,
                include_varsettings=not self.selected_doc.IsLinked
                )
        self.printsettings_cb.ItemsSource = psetting_items

        print_mgr = self._get_printmanager()
        if isinstance(print_mgr.PrintSetup.CurrentPrintSetting,
                      DB.InSessionPrintSetting):
            in_session = PrintSettingListItem(
                print_mgr.PrintSetup.CurrentPrintSetting
                )
            psetting_items.append(in_session)
            self.printsettings_cb.SelectedItem = in_session
        else:
            self._init_psettings = print_mgr.PrintSetup.CurrentPrintSetting
            cur_psetting_name = print_mgr.PrintSetup.CurrentPrintSetting.Name
            for psetting_item in psetting_items:
                if psetting_item.name == cur_psetting_name:
                    self.printsettings_cb.SelectedItem = psetting_item

        if self.selected_doc.IsLinked:
            self.disable_element(self.printsettings_cb)
        else:
            self.enable_element(self.printsettings_cb)

        self._update_combine_option()

    def _update_combine_option(self):
        self.enable_element(self.combine_cb)
        if not self.export_pdf_enabled \
                or self.selected_doc.IsLinked \
                or ((self.selected_sheetlist and self.has_print_settings)
                    and self.selected_print_setting.allows_variable_paper):
            self.disable_element(self.combine_cb)
            self.combine_cb.IsChecked = False

    def _setup_sheet_list(self):
        sheet_indices = self._get_sheet_index_list()
        try:
            cl = DB.FilteredElementCollector(self.selected_doc)
            sheetsets = cl.OfClass(framework.get_type(DB.ViewSheetSet)) \
                        .WhereElementIsNotElementType() \
                        .ToElements()
            for ss in sheetsets:
                sheet_indices.append(SheetSetList(ss))
        except Exception as e:
            logger.warning("Could not load sheet sets: {}".format(e))
        sheet_indices.append(AllSheetsList())
        sheet_indices.append(UnlistedSheetsList())

        self.schedules_cb.ItemsSource = sheet_indices
        self.schedules_cb.SelectedIndex = 0
        if self.schedules_cb.ItemsSource:
            self.enable_element(self.schedules_cb)
        else:
            self.disable_element(self.schedules_cb)

    def _get_output_root(self):
        try:
            if hasattr(self, 'output_dir_tb') and self.output_dir_tb and self.output_dir_tb.Text:
                return self.output_dir_tb.Text
        except Exception:
            pass
        return PrintUtils.get_dir()

    def _get_output_dir(self, task="_PRINT"):
        base = self._get_output_root()
        return PrintUtils.ensure_dir(op.join(base, PrintUtils.get_folder(task)))

    def _ensure_pdf_extension(self, value):
        if not value:
            return value
        val_lower = value.lower()
        if val_lower.endswith('.dwg'):
            value = value[:-4]
            val_lower = value.lower()
        if not val_lower.endswith('.pdf'):
            return value + '.pdf'
        return value

    def _load_excel_rows(self):
        self._excel_rows_by_name = {}
        self._excel_rows_by_number = {}
        path = ''
        try:
            path = self.excel_path_tb.Text if hasattr(self, 'excel_path_tb') else ''
        except Exception:
            path = ''
        path = self._normalize_excel_path(path)
        self._excel_path = path
        if not path:
            return
        resolved_path = self._resolve_excel_path(path)
        if not resolved_path:
            forms.alert("Excel file not found:\n{}".format(path))
            return
        if resolved_path != path:
            path = resolved_path
            self._excel_path = path
            try:
                self.excel_path_tb.Text = path
            except Exception:
                pass
        try:
            rows = ExcelDatabase.read_print_rows(path)
        except Exception as ex:
            forms.alert("Failed to read Excel file:\n{}\n{}".format(path, ex))
            return

        for row in rows:
            drawing_name_key = normalize_match_text(row.DrawingName)
            drawing_number_key = normalize_match_text(row.DrawingNumber)
            if drawing_name_key and drawing_name_key not in self._excel_rows_by_name:
                self._excel_rows_by_name[drawing_name_key] = row
            if drawing_number_key and drawing_number_key not in self._excel_rows_by_number:
                self._excel_rows_by_number[drawing_number_key] = row

    def _get_excel_row(self, sheet):
        if not sheet:
            return None
        try:
            sheet_name_key = normalize_match_text(sheet.name)
            sheet_number_key = normalize_match_text(sheet.number)
            if sheet_name_key in self._excel_rows_by_name:
                return self._excel_rows_by_name.get(sheet_name_key)
            if sheet_number_key in self._excel_rows_by_number:
                return self._excel_rows_by_number.get(sheet_number_key)
        except Exception:
            return None
        return None

    def _normalize_excel_path(self, path):
        path = (path or '').strip().strip('"')
        if not path:
            return ''
        normalized = op.abspath(op.expanduser(path))
        if not op.splitext(normalized)[1]:
            normalized += '.csv'
        return normalized

    def _resolve_excel_path(self, path):
        normalized = self._normalize_excel_path(path)
        if not normalized:
            return ''
        if op.exists(normalized):
            return normalized
        base, _ = op.splitext(normalized)
        for candidate_ext in ('.xlsx', '.xlsm', '.xls', '.csv'):
            candidate = base + candidate_ext
            if op.exists(candidate):
                return candidate
        return ''

    def _pick_excel_file_path(self):
        try:
            dialog = OpenFileDialog()
            dialog.Filter = "Database Files (*.xlsx;*.xlsm;*.xls;*.csv)|*.xlsx;*.xlsm;*.xls;*.csv|Excel Files (*.xlsx;*.xlsm;*.xls)|*.xlsx;*.xlsm;*.xls|CSV Files (*.csv)|*.csv|All Files (*.*)|*.*"
            dialog.Multiselect = False
            dialog.CheckFileExists = True
            if dialog.ShowDialog():
                return dialog.FileName
        except Exception:
            pass
        return forms.pick_file(file_ext='csv', multi_file=False, title='Select CSV Database')

    def _save_excel_file_path(self, current_path=''):
        try:
            dialog = SaveFileDialog()
            dialog.Filter = "CSV Files (*.csv)|*.csv|Excel Files (*.xlsx)|*.xlsx|All Files (*.*)|*.*"
            dialog.DefaultExt = "csv"
            dialog.AddExtension = True
            dialog.OverwritePrompt = True
            normalized_current = self._normalize_excel_path(current_path)
            if normalized_current:
                current_dir = op.dirname(normalized_current)
                if current_dir and op.exists(current_dir):
                    dialog.InitialDirectory = current_dir
                dialog.FileName = op.basename(normalized_current)
            else:
                dialog.FileName = "PrintDatabase.csv"
            if dialog.ShowDialog():
                return dialog.FileName
        except Exception:
            pass
        return forms.save_file(file_ext='csv', title='Save CSV Database', default_name='PrintDatabase.csv')

    def browse_excel(self, sender, args):
        excel_file = self._pick_excel_file_path()
        if not excel_file:
            return
        excel_file = self._normalize_excel_path(excel_file)
        try:
            self.excel_path_tb.Text = excel_file
        except Exception:
            pass
        self._load_excel_rows()
        self.options_changed(None, None)

    def generate_excel(self, sender, args):
        path = ''
        try:
            path = self.excel_path_tb.Text
        except Exception:
            path = ''
        path = self._normalize_excel_path(path)
        if not path:
            path = self._save_excel_file_path(path)
            if not path:
                return
        path = self._normalize_excel_path(path)
        parent_dir = op.dirname(path)
        if parent_dir and not op.exists(parent_dir):
            os.makedirs(parent_dir)
        try:
            self.excel_path_tb.Text = path
        except Exception:
            pass

        sheets = []
        try:
            if self.sheet_list:
                sheets = [x.revit_sheet for x in self.sheet_list if x and x.revit_sheet]
        except Exception:
            sheets = []
        if not sheets:
            try:
                sheets = DB.FilteredElementCollector(self.selected_doc)\
                         .OfClass(framework.get_type(DB.ViewSheet))\
                         .WhereElementIsNotElementType()\
                         .ToElements()
            except Exception:
                sheets = []
        try:
            path = op.abspath(ExcelDatabase.generate_or_update(path, sheets))
            self.excel_path_tb.Text = path
            forms.alert("Excel updated:\n{}".format(path), ok=True)
        except Exception as ex:
            forms.alert("CSV update failed:\n{}\n{}".format(path, ex))
            return

        self._load_excel_rows()
        self.options_changed(None, None)

    def browse_output(self, sender, args):
        current = ''
        try:
            current = self.output_dir_tb.Text
        except Exception:
            current = ''
        folder = FolderPicker.pick_folder(current)
        if not folder:
            return
        try:
            self.output_dir_tb.Text = folder
        except Exception:
            pass

    def _verify_print_filename(self, sheet_name, sheet_print_filepath):
        if op.exists(sheet_print_filepath):
            logger.warning(
                "Skipping sheet \"%s\" "
                "File already exist at %s.",
                sheet_name, sheet_print_filepath
                )
            return False
        return True

    def _print_combined_sheets_in_order(self, target_sheets):
        if not self.export_pdf_enabled:
            forms.alert("Export PDF is disabled.")
            return
        # make sure we can access the print config
        print_mgr = self._get_printmanager()
        if not print_mgr:
            forms.alert(
                "Error getting print manager for this document",
                exitscript=True
                )
        with revit.TransactionGroup('Print Sheets in Order',
                                    doc=self.selected_doc):
            with revit.Transaction('Set Printer Settings',
                                   doc=self.selected_doc,
                                   log_errors=False):
                try:
                    print_mgr.PrintSetup.CurrentPrintSetting = \
                        self.selected_print_setting.print_settings
                    print_mgr.SelectNewPrintDriver(self.selected_printer)
                    print_mgr.PrintRange = DB.PrintRange.Select
                except Exception as cpSetEx:
                    forms.alert(
                        "Print setting is incompatible with printer.",
                        expanded=str(cpSetEx)
                        )
                    return
            # The OrderedViewList property was added to the IViewSheetSet
            # interface in Revit 2023 and makes the non-printable char
            # technique unnecessary.
            supports_OrderedViewList = HOST_APP.is_newer_than(2022)
            if supports_OrderedViewList:
                sheet_list = List[DB.View]()
                for sheet in target_sheets:
                    if sheet.printable:
                        sheet_list.Add(sheet.revit_sheet)
            else:
                # add non-printable char in front of sheet Numbers
                # to push revit to sort them per user
                sheet_set = DB.ViewSet()
                original_sheetnums = []
                with revit.Transaction('Fix Sheet Numbers',
                                    doc=self.selected_doc):
                    for idx, sheet in enumerate(target_sheets):
                        rvtsheet = sheet.revit_sheet
                        # removing any NPC from previous failed prints
                        if NPC in rvtsheet.SheetNumber:
                            rvtsheet.SheetNumber = \
                                rvtsheet.SheetNumber.replace(NPC, '')
                        # create a list of the existing sheet numbers
                        original_sheetnums.append(rvtsheet.SheetNumber)
                        # add a prefix (NPC) for sorting purposes
                        rvtsheet.SheetNumber = \
                            NPC * (idx + 1) + rvtsheet.SheetNumber
                        if sheet.printable:
                            sheet_set.Insert(rvtsheet)

            # Collect existing sheet sets
            cl = DB.FilteredElementCollector(self.selected_doc)
            viewsheetsets = cl.OfClass(framework.get_type(DB.ViewSheetSet))\
                              .WhereElementIsNotElementType()\
                              .ToElements()
            all_viewsheetsets = {vss.Name: vss for vss in viewsheetsets}

            sheetsetname = 'OrderedPrintSet'

            with revit.Transaction('Remove Previous Print Set',
                                   doc=self.selected_doc):
                # Delete existing matching sheet set
                if sheetsetname in all_viewsheetsets:
                    print_mgr.ViewSheetSetting.CurrentViewSheetSet = \
                        all_viewsheetsets[sheetsetname]
                    print_mgr.ViewSheetSetting.Delete()

            with revit.Transaction('Update Ordered Print Set',
                                   doc=self.selected_doc):
                try:
                    viewsheet_settings = print_mgr.ViewSheetSetting
                    if supports_OrderedViewList:
                        viewsheet_settings.CurrentViewSheetSet.IsAutomatic = False
                        viewsheet_settings.CurrentViewSheetSet.OrderedViewList = \
                            sheet_list
                    else:
                        viewsheet_settings.CurrentViewSheetSet.Views = \
                            sheet_set
                    viewsheet_settings.SaveAs(sheetsetname)
                except Exception as viewset_err:
                    sheet_report = ''
                    for sheet in sheet_set:
                        sheet_report += '{} {}\n'.format(
                            sheet.SheetNumber if isinstance(sheet,
                                                            DB.ViewSheet)
                            else '---',
                            type(sheet)
                            )
                    logger.critical(
                        'Error setting sheet set on print mechanism. '
                        'These items are included in the viewset '
                        'object:\n%s', sheet_report
                        )
                    raise viewset_err

            # set print job configurations
            print_mgr.PrintOrderReverse = self.reverse_print
            try:
                print_mgr.CombinedFile = True
            except Exception as e:
                forms.alert(str(e) +
                            '\nSet printer correctly in Print settings.')
                script.exit()
            dir_path = self._get_output_dir("_PRINT")
            print_filepath = op.join(dir_path, 'Ordered Sheet Set.pdf')
            print_mgr.PrintToFile = True
            print_mgr.PrintToFileName = print_filepath

            with revit.Transaction('Reload Keynote File',
                                   doc=self.selected_doc):
                DB.KeynoteTable.GetKeynoteTable(revit.doc).Reload(None)
            
            print_mgr.Apply()
            print_mgr.SubmitPrint()


            if not supports_OrderedViewList:
                # now fix the sheet names
                with revit.Transaction('Restore Sheet Numbers',
                                    doc=self.selected_doc):
                    for sheet, sheetnum in zip(target_sheets,
                                            original_sheetnums):
                        rvtsheet = sheet.revit_sheet
                        rvtsheet.SheetNumber = sheetnum

            self._reset_psettings()

    def _print_sheets_in_order(self, target_sheets):
        # make sure we can access the print config
        print_mgr = self._get_printmanager()
        print_mgr.PrintToFile = True
        per_sheet_psettings = self.selected_print_setting.allows_variable_paper

        # make sure you can print, construct print path and make directory
        dirPath = self._get_output_dir("_PRINT")
        doc = self.selected_doc

        if not self.export_pdf_enabled and not self.export_dwg_enabled:
            forms.alert("Enable PDF and/or DWG export.")
            return

        if self.export_pdf_enabled or self.export_dwg_enabled:
            PrintUtils.open_dir(dirPath)
        else:
            return


        with revit.Transaction('Reload Keynote File',
                               doc=self.selected_doc):
            DB.KeynoteTable.GetKeynoteTable(self.selected_doc).Reload(None)

        with revit.DryTransaction('Set Printer Settings',
                                  doc=self.selected_doc):
            try:
                if not per_sheet_psettings:
                    print_mgr.PrintSetup.CurrentPrintSetting = \
                        self.selected_print_setting.print_settings
                print_mgr.SelectNewPrintDriver(self.selected_printer)
                print_mgr.PrintRange = DB.PrintRange.Current
            except Exception as cpSetEx:
                forms.alert(
                    "Print setting is incompatible with printer.",
                    expanded=str(cpSetEx)
                    )
                return
            if target_sheets:
                if self.export_pdf_enabled and self.export_dwg_enabled:
                    with forms.ProgressBar(step=1, title='Exporting PDF & DWGs... ' + '{value} of {max_value}', cancellable=(not self._scheduled_execution)) as pb1:
                        pbTotal1 = len(target_sheets) * 2
                        pbCount1 = 1
                        for sheet in target_sheets:
                            if pb1.cancelled:
                                break
                            else:
                                if sheet.printable:
                                    if sheet.print_filename:
                                        print_filepath = op.join(dirPath, sheet.print_filename)
                                        print_mgr.PrintToFileName = print_filepath

                                        if per_sheet_psettings:
                                            print_mgr.PrintSetup.CurrentPrintSetting = \
                                                sheet.print_settings

                                        if self._verify_print_filename(sheet.name,
                                                                    print_filepath):
                                            try:
                                                pb1.update_progress(pbCount1, pbTotal1)
                                                pbCount1 += 1
                                                if IS_REVIT_2022_OR_NEWER:
                                                    optspdf = PrintUtils.pdf_opts()
                                                    PrintUtils.export_sheet_pdf(dirPath, sheet.revit_sheet, optspdf, doc, sheet.print_filename)
                                                else:
                                                    print_mgr.SubmitPrint(sheet.revit_sheet)
                                            except Exception as e:
                                                logger.error('Failed to export PDF for sheet %s: %s', sheet.number, e)

                                            try:
                                                pb1.update_progress(pbCount1, pbTotal1)
                                                pbCount1 += 1
                                                optsdwg = PrintUtils.dwg_opts()
                                                PrintUtils.export_sheet_dwg(dirPath, sheet.revit_sheet, optsdwg, doc, sheet.print_filename)
                                            except Exception as e:
                                                logger.error('Failed to export DWG for sheet %s: %s', sheet.number, e)
                                    else:
                                        pbCount1 += 2
                                        logger.debug(
                                            'Sheet %s does not have a valid file name.',
                                            sheet.number)
                                else:
                                    pbCount1 += 2
                                    logger.debug('Sheet %s is not printable. Skipping print.',
                                                sheet.number)
                elif self.export_pdf_enabled:
                    with forms.ProgressBar(step=1, title='Exporting PDFs... ' + '{value} of {max_value}', cancellable=(not self._scheduled_execution)) as pb1:
                        pbTotal1 = len(target_sheets)
                        pbCount1 = 1
                        for sheet in target_sheets:
                            if pb1.cancelled:
                                break
                            else:
                                if sheet.printable:
                                    if sheet.print_filename:
                                        print_filepath = op.join(dirPath, sheet.print_filename)
                                        print_mgr.PrintToFileName = print_filepath

                                        if per_sheet_psettings:
                                            print_mgr.PrintSetup.CurrentPrintSetting = \
                                                sheet.print_settings

                                        if self._verify_print_filename(sheet.name,
                                                                    print_filepath):
                                            try:
                                                pb1.update_progress(pbCount1, pbTotal1)
                                                pbCount1 += 1
                                                if IS_REVIT_2022_OR_NEWER:
                                                    optspdf = PrintUtils.pdf_opts()
                                                    PrintUtils.export_sheet_pdf(dirPath, sheet.revit_sheet, optspdf, doc, sheet.print_filename)
                                                else:
                                                    print_mgr.SubmitPrint(sheet.revit_sheet)
                                            except Exception as e:
                                                logger.error('Failed to export PDF for sheet %s: %s', sheet.number, e)

                                    else:
                                        pbCount1 += 1
                                        logger.debug(
                                            'Sheet %s does not have a valid file name.',
                                            sheet.number)
                                else:
                                    pbCount1 += 1
                                    logger.debug('Sheet %s is not printable. Skipping print.',
                                                sheet.number)
                elif self.export_dwg_enabled:
                    with forms.ProgressBar(step=1, title='Exporting DWGs... ' + '{value} of {max_value}', cancellable=(not self._scheduled_execution)) as pb1:
                        pbTotal1 = len(target_sheets)
                        pbCount1 = 1
                        for sheet in target_sheets:
                            if pb1.cancelled:
                                break
                            else:
                                if sheet.printable:
                                    if sheet.print_filename:
                                        print_filepath = op.join(dirPath, sheet.print_filename)
                                        if per_sheet_psettings:
                                            print_mgr.PrintSetup.CurrentPrintSetting = \
                                                sheet.print_settings
                                        if self._verify_print_filename(sheet.name, print_filepath):
                                            try:
                                                pb1.update_progress(pbCount1, pbTotal1)
                                                pbCount1 += 1
                                                optsdwg = PrintUtils.dwg_opts()
                                                PrintUtils.export_sheet_dwg(dirPath, sheet.revit_sheet, optsdwg, doc, sheet.print_filename)
                                            except Exception as e:
                                                logger.error('Failed to export DWG for sheet %s: %s', sheet.number, e)
                                    else:
                                        pbCount1 += 1
                                        logger.debug(
                                            'Sheet %s does not have a valid file name.',
                                            sheet.number)
                                else:
                                    pbCount1 += 1
                                    logger.debug('Sheet %s is not printable. Skipping print.',
                                                sheet.number)

    def _print_linked_sheets_in_order(self, target_sheets, target_doc):
        if not self.export_pdf_enabled:
            forms.alert("Export PDF is disabled.")
            return
        # make sure we can access the print config
        print_mgr = self._get_printmanager()
        print_mgr.PrintToFile = True
        print_mgr.SelectNewPrintDriver(self.selected_printer)
        print_mgr.PrintRange = DB.PrintRange.Current


        dirPath = self._get_output_dir("_PRINT")
        doc = target_doc

        if IS_REVIT_2022_OR_NEWER:
            PrintUtils.open_dir(dirPath)
        else:
            return

        if target_sheets:
            with forms.ProgressBar(step=1, title='Exporting Linked PDFs... ' + '{value} of {max_value}', cancellable=(not self._scheduled_execution)) as pb1:
                
                pbTotal1 = len(target_sheets)
                pbCount1 = 1
                for sheet in target_sheets:
                    if pb1.cancelled:
                        break
                    else:
                        if sheet.printable:
                            if sheet.print_filename:
                                print_filepath = op.join(dirPath, sheet.print_filename)
                                print_mgr.PrintToFileName = print_filepath

                                if self._verify_print_filename(sheet.name,
                                                            print_filepath):
                                    try:
                                        pb1.update_progress(pbCount1, pbTotal1)
                                        pbCount1 += 1
                                        if IS_REVIT_2022_OR_NEWER:
                                            optspdf = PrintUtils.pdf_opts()
                                            PrintUtils.export_sheet_pdf(dirPath, sheet.revit_sheet, optspdf, doc, sheet.print_filename)
                                        else:
                                            print_mgr.SubmitPrint(sheet.revit_sheet)
                                    except Exception as e:
                                        logger.error('Failed to export PDF for sheet %s: %s', sheet.number, e)
                            else:
                                pbCount1 += 1
                                logger.debug(
                                    'Sheet %s does not have a valid file name.',
                                    sheet.number)
                        else:
                            pbCount1 += 1
                            logger.debug('Sheet %s is not printable. Skipping print.',
                                        sheet.number)
                                    
    def _reset_error(self):
        self.enable_element(self.print_b)
        self.hide_element(self.errormsg_block)
        self.errormsg_tb.Text = ''

    def _set_error(self, err_msg):
        if self.errormsg_tb.Text != err_msg:
            self.disable_element(self.print_b)
            self.show_element(self.errormsg_block)
            self.errormsg_tb.Text = err_msg

    def _update_print_indices(self, sheet_list):
        start_idx = self.index_start
        for idx, sheet in enumerate(sheet_list):
            sheet.print_index = INDEX_FORMAT\
                .format(digits=self.index_digits)\
                .format(idx + start_idx)

    def _update_filename_template(self, template, value_type, value_getter):
        finder_pattern = r'{' + value_type + r':(.*?)}'
        for param_name in re.findall(finder_pattern, template):
            param_value = value_getter(param_name)
            repl_pattern = r'{' + value_type + ':' + param_name + r'}'
            if param_value:
                template = re.sub(repl_pattern, str(param_value), template)
            template = re.sub(repl_pattern, '', template)
        return template

    def _update_print_filename(self, template, sheet, excel_row=None):
        # resolve sheet-level custom param values
        ## get titleblock param values
        template = self._update_filename_template(
            template=template,
            value_type='tblock_param',
            value_getter=lambda x: revit.query.get_param_value(
                    revit.query.get_param(sheet.revit_tblock, x)
                ) or revit.query.get_param_value(
                    revit.query.get_param(sheet.revit_tblock_type, x)
                )
        )

        ## get sheet param values
        template = self._update_filename_template(
            template=template,
            value_type='sheet_param',
            value_getter=lambda x: revit.query.get_param_value(
                revit.query.get_param(sheet.revit_sheet, x)
                )
        )

        ## get date for sortable list
        rev_date_str = sheet.revision.date or ""
        sortable_date = ""

        # Try to detect user's locale
        locale_tuple = locale.getdefaultlocale()
        user_locale = (locale_tuple[0] if locale_tuple and locale_tuple[0] else "en_GB")
        dayfirst = not user_locale.startswith("en_US")

        # Try several common patterns
        date_formats = ["%d.%m.%y", "%m.%d.%y", "%d/%m/%y", "%m/%d/%y"]
        if not dayfirst:
            date_formats = ["%m.%d.%y", "%m/%d/%y", "%d.%m.%y", "%d/%m/%y"]

        for fmt in date_formats:
            try:
                parsed = datetime.datetime.strptime(rev_date_str, fmt)
                sortable_date = parsed.strftime("%Y%m%d")
                break
            except (ValueError, TypeError):
                continue

        sheet.revision_date_sortable = sortable_date
        

        # resolved the fixed formatters
        try:
            output_fname = \
                template.format(
                    index=sheet.print_index,
                    number=sheet.number,
                    name=sheet.name,
                    name_dash=sheet.name.replace(' ', '-'),
                    name_underline=sheet.name.replace(' ', '_'),
                    current_date=coreutils.current_date(),
                    issue_date=sheet.issue_date,
                    rev_number=sheet.revision.number if sheet.revision else '',
                    rev_desc=sheet.revision.desc if sheet.revision else '',
                    rev_date=sheet.revision.date if sheet.revision else '',
                    proj_name=self.project_info.name,
                    proj_number=self.project_info.number,
                    proj_building_name=self.project_info.building_name,
                    proj_issue_date=self.project_info.issue_date,
                    proj_org_name=self.project_info.org_name,
                    proj_status=self.project_info.status,
                    username=HOST_APP.username,
                    revit_version=HOST_APP.version,
                    excel_name=excel_row.DrawingName if excel_row else '',
                    excel_number=excel_row.DrawingNumber if excel_row else '',
                )
        except Exception as ferr:
            if excel_row and excel_row.PrintFileName:
                output_fname = self._ensure_pdf_extension(excel_row.PrintFileName)
            else:
                output_fname = ''
                if isinstance(ferr, KeyError):
                    self._set_error('Unknown key in selected naming format')
        # and set the sheet file name
        sheet.print_filename = output_fname

    def _update_print_filenames(self, sheet_list):
        doc = self.selected_doc
        naming_fmt = self.selected_naming_format
        base_template = naming_fmt.template if naming_fmt else ''
        try:
            current_excel_path = self.excel_path_tb.Text if hasattr(self, 'excel_path_tb') else ''
        except Exception:
            current_excel_path = ''
        if current_excel_path and current_excel_path != self._excel_path:
            self._load_excel_rows()

        for sheet in sheet_list:
            excel_row = self._get_excel_row(sheet)
            sheet_template = excel_row.PrintFileName if excel_row and excel_row.PrintFileName else base_template
            sheet_template = self._ensure_pdf_extension(sheet_template)

            # resolve project-level custom param values
            sheet_template = self._update_filename_template(
                template=sheet_template,
                value_type='proj_param',
                value_getter=lambda x: revit.query.get_param_value(
                    doc.ProjectInformation.LookupParameter(x)
                    )
            )

            sheet_template = self._update_filename_template(
                template=sheet_template,
                value_type='glob_param',
                value_getter=lambda x: revit.query.get_param_value(
                    revit.query.get_global_parameter(x, doc=doc)
                    )
            )

            self._update_print_filename(sheet_template, sheet, excel_row)

    def _find_sheet_tblock(self, revit_sheet, tblocks):
        for tblock in tblocks:
            view_sheet = revit_sheet.Document.GetElement(tblock.OwnerViewId)
            if view_sheet.Id == revit_sheet.Id:
                return tblock

    def _get_sheet_printsettings(self, tblocks, psettings):
        tblock_printsettings = {}
        sheet_printsettings = {}
        for tblock in tblocks:
            tblock_psetting = None
            sheet = self.selected_doc.GetElement(tblock.OwnerViewId)
            # build a unique id for this tblock
            tblock_tform = tblock.GetTotalTransform()
            tblock_tid = get_elementid_value(tblock.GetTypeId())
            tblock_tid = tblock_tid * 100 \
                         + tblock_tform.BasisX.X * 10 \
                         + tblock_tform.BasisX.Y
            # can not use None as default. see notes below
            tblock_psetting = tblock_printsettings.get(tblock_tid, None)
            # if found a tblock print settings, assign that to sheet
            if tblock_psetting:
                sheet_printsettings[sheet.SheetNumber] = tblock_psetting
            # otherwise, analyse the tblock and determine print settings
            else:
                # try the type parameter "Print Setting"
                tblock_type = tblock.Document.GetElement(tblock.GetTypeId())
                if tblock_type:
                    psparam = tblock_type.LookupParameter("Print Setting")
                    if psparam:
                        psetting_name = psparam.AsString()
                        psparam_psetting = \
                            next(
                                (x for x in psettings
                                    if x.Name == psetting_name),
                                None
                            )
                        if psparam_psetting:
                            tblock_psetting = \
                                TitleBlockPrintSettings(
                                    psettings=[psparam_psetting],
                                    set_by_param=True
                                )
                # otherwise, try to detect applicable print settings
                # based on title block geometric properties
                if not tblock_psetting:
                    tblock_psetting = \
                        TitleBlockPrintSettings(
                            psettings=revit.query.get_titleblock_print_settings(
                                tblock,
                                self.selected_printer,
                                psettings
                                ),
                            set_by_param=False
                        )
                # the analysis result might be None
                tblock_printsettings[tblock_tid] = tblock_psetting
                sheet_printsettings[sheet.SheetNumber] = tblock_psetting
        return sheet_printsettings

    def _reset_psettings(self):
        if self._init_psettings:
            print_mgr = self._get_printmanager()
            with revit.Transaction("Revert to Original Print Settings"):
                print_mgr.PrintSetup.CurrentPrintSetting = self._init_psettings

    def _update_index_slider(self):
        index_digits = \
            coreutils.get_integer_length(
                len(self._scheduled_sheets) + self.index_start
                )
        self.index_slider.Minimum = max([index_digits, 2])
        self.index_slider.Maximum = self.index_slider.Minimum + 3

    # event handlers
    def doclist_changed(self, sender, args):
        self.project_info = revit.query.get_project_info(doc=self.selected_doc)
        self._setup_printers()
        self._setup_print_settings()
        self._setup_sheet_list()

    def sheetlist_changed(self, sender, args):
        print_settings = None
        tblocks = revit.query.get_elements_by_categories(
            [DB.BuiltInCategory.OST_TitleBlocks],
            doc=self.selected_doc
        )
        if self.selected_sheetlist and self.has_print_settings:
            rev_cfg = DB.RevisionSettings.GetRevisionSettings(revit.doc)
            if self.selected_print_setting.allows_variable_paper:
                sheet_printsettings = \
                    self._get_sheet_printsettings(
                        tblocks,
                        revit.query.get_all_print_settings(
                            doc=self.selected_doc
                            )
                        )
                self.show_element(self.varsizeguide)
                self.show_element(self.psettingcol)
                self._scheduled_sheets = [
                    ViewSheetListItem(
                        view_sheet=x,
                        view_tblock=self._find_sheet_tblock(x, tblocks),
                        print_settings=sheet_printsettings.get(
                            x.SheetNumber,
                            None),
                        rev_settings=rev_cfg)
                    for x in self.selected_sheetlist.get_sheets(
                        doc=self.selected_doc
                        )
                    ]
            else:
                print_settings = self.selected_print_setting.print_settings
                self.hide_element(self.varsizeguide)
                self.hide_element(self.psettingcol)
                self._scheduled_sheets = [
                    ViewSheetListItem(
                        view_sheet=x,
                        view_tblock=self._find_sheet_tblock(x, tblocks),
                        print_settings=TitleBlockPrintSettings(
                            psettings=[print_settings],
                            set_by_param=False
                        ),
                        rev_settings=rev_cfg)
                    for x in self.selected_sheetlist.get_sheets(
                        doc=self.selected_doc
                        )
                    ]
        self._update_combine_option()
        # self._update_index_slider()
        self.options_changed(None, None)

    def printers_changed(self, sender, args):
        print_mgr = self._get_printmanager()
        print_mgr.SelectNewPrintDriver(self.selected_printer)
        self._setup_print_settings()

    def options_changed(self, sender, args):
        self._reset_error()

        # update index digit range
        self._update_index_slider()
        self._update_combine_option()

        # reverse sheet if reverse is set
        sheet_list = [x for x in self._scheduled_sheets]
        if self.reverse_print:
            sheet_list.reverse()

        if self.combine_cb.IsChecked:
            self.hide_element(self.order_sp)
            self.hide_element(self.namingformat_dp)
            self.hide_element(self.pfilename)
            if hasattr(self, 'export_dwg'):
                self.export_dwg.IsChecked = False
                self.export_dwg.IsEnabled = False
        else:
            self.show_element(self.order_sp)
            self.show_element(self.namingformat_dp)
            self.show_element(self.pfilename)
            if hasattr(self, 'export_dwg'):
                self.export_dwg.IsEnabled = True

        if self.selected_doc.IsLinked:
            if hasattr(self, 'export_dwg'):
                self.export_dwg.IsChecked = False
                self.export_dwg.IsEnabled = False

        if not self.export_pdf_enabled:
            self.combine_cb.IsChecked = False

        # decide whether to show the placeholders or not
        if not self.show_placeholders:
            self.indexspace_cb.IsEnabled = True
            # update print indices with placeholder sheets
            self._update_print_indices(sheet_list)
            # remove placeholders if requested
            printable_sheets = []
            for sheet in sheet_list:
                if sheet.printable:
                    printable_sheets.append(sheet)
            # update print indices without placeholder sheets
            if not self.include_placeholders:
                self._update_print_indices(printable_sheets)
            self.sheet_list = printable_sheets
        else:
            self.indexspace_cb.IsChecked = True
            self.indexspace_cb.IsEnabled = False
            # update print indices
            self._update_print_indices(sheet_list)
            # Show all sheets
            self.sheet_list = sheet_list

        # update sheet naming formats
        self._update_print_filenames(sheet_list)

    def set_sheet_printsettings(self, sender, args):
        if self.selected_printable_sheets:
            # make sure none of the sheets has readonly print setting
            if any(x.read_only for x in self.selected_printable_sheets):
                forms.alert("Print settings has been set by titleblock "
                            "for one or more sheets and can only be changed "
                            "by modifying the titleblock print setting")
                return

            all_psettings = \
                [x for x in self.print_settings if x.is_user_defined]
            sheet_psettings = \
                self.selected_printable_sheets[0].all_print_settings
            if sheet_psettings:
                options = {
                    'Matching Print Settings':
                        self._get_psetting_items(
                            doc=self.selected_doc,
                            psettings=sheet_psettings
                            ),
                    'All Print Settings':
                        all_psettings
                }
            else:
                options = all_psettings or []

            if options:
                psetting_item = forms.SelectFromList.show(
                    options,
                    name_attr='name',
                    group_selector_title='Print Settings:',
                    default_group='Matching Print Settings',
                    title='Select Print Setting',
                    item_container_template=self.Resources["printSettingsItem"],
                    width=450, height=400
                    )
                if psetting_item:
                    for sheet in self.selected_printable_sheets:
                        sheet.print_settings = psetting_item
            else:
                forms.alert('There are no print settings in this model.')

    def sheet_selection_changed(self, sender, args):
        if self.selected_printable_sheets:
            return self.enable_element(self.sheetopts_wp)
        self.disable_element(self.sheetopts_wp)

    def validate_index_start(self, sender, args):
        args.Handled = re.match(r'[^0-9]+', args.Text)

    def rest_index(self, sender, args):
        self.indexstart_tb.Text = '0'

    def edit_formats(self, sender, args):
        editfmt_wnd = \
            EditNamingFormatsWindow(
                'EditNamingFormats.xaml',
                start_with=self.selected_naming_format,
                doc=self.selected_doc
                )
        editfmt_wnd.show_dialog()
        self.namingformat_cb.ItemsSource = editfmt_wnd.naming_formats
        self.namingformat_cb.SelectedItem = editfmt_wnd.selected_naming_format

    def copy_filenames(self, sender, args):
        if self.selected_sheets:
            filenames = [x.print_filename for x in self.selected_sheets]
            script.clipboard_copy('\n'.join(filenames))

    def _is_csv_writeback_enabled(self):
        try:
            if hasattr(self, 'excel_writeback_cb') and self.excel_writeback_cb:
                return bool(self.excel_writeback_cb.IsChecked)
        except Exception:
            pass
        return False

    def _validate_excel_path(self, require_nonempty=False):
        try:
            path = self.excel_path_tb.Text
        except Exception:
            path = ''
        path = self._normalize_excel_path(path)
        if not path:
            if require_nonempty:
                forms.alert('Pick a CSV file path before scheduling.')
                return False
            return True
        resolved_path = self._resolve_excel_path(path)
        if not resolved_path:
            forms.alert('CSV file not found:\n{}'.format(path))
            return False
        if resolved_path != path:
            try:
                self.excel_path_tb.Text = resolved_path
            except Exception:
                pass
        return True

    def _validate_export_options(self):
        if not self.export_pdf_enabled and not self.export_dwg_enabled:
            forms.alert('Enable PDF and/or DWG export.')
            return False
        return True

    def _get_target_sheets(self, ask_user=True):
        if not self.sheet_list:
            return None
        selected_only = False
        if self.selected_sheets and ask_user:
            opts = forms.alert(
                "You have a series of sheets selected. Do you want to "
                "print the selected sheets or all sheets?",
                options=["Only Selected Sheets", "All Scheduled Sheets"]
                )
            selected_only = opts == "Only Selected Sheets"
        elif self.selected_sheets and not ask_user:
            selected_only = True

        return self.selected_sheets if selected_only else self.sheet_list

    def _run_print(self, target_sheets, confirm=True, close_window=True):
        if not target_sheets:
            return
        is_scheduled_run = (not confirm)
        if not self._validate_export_options():
            return
        if not self._validate_excel_path():
            return
        self._load_excel_rows()
        self.options_changed(None, None)

        if not self.combine_print:
            if self.selected_print_setting.allows_variable_paper \
                and not all(x.print_settings for x in target_sheets):
                forms.alert(
                    'Not all sheets have a print setting assigned to them. '
                    'Select sheets and assign print settings.')
                return
            if confirm:
                printable_count = len([x for x in target_sheets if x.printable])
                if printable_count > 5:
                    sheet_count = len(target_sheets)
                    message = str(printable_count)
                    if printable_count != sheet_count:
                        message += ' (out of {} total)'.format(sheet_count)

                    if not forms.alert('Are you sure you want to print {} '
                                       'sheets individually? The process can '
                                       'not be cancelled.'.format(message),
                                       ok=False, yes=True, no=True):
                        return

        writeback_enabled = self._is_csv_writeback_enabled()

        # Write Excel first so print outputs and database stay in sync.
        # If writeback is enabled and fails, stop before printing.
        if writeback_enabled:
            # Keep manual runs interactive, but avoid CSV popups during scheduled runs.
            show_csv_alerts = bool(confirm) and (not self._suppress_csv_popups)
            wrote = self._write_excel_report(target_sheets,
                                             require_writeback_opt=True,
                                             allow_create=False,
                                             recompute_names=True,
                                             show_alert=show_csv_alerts,
                                             dry_run_mode=False)
            if not wrote:
                return

        if is_scheduled_run:
            self._set_schedule_status_text("Syncing before scheduled print...")
            if not self._sync_before_scheduled_print():
                self._set_schedule_status_text("Scheduled print cancelled: pre-sync failed.")
                return

        prev_scheduled_execution = self._scheduled_execution
        self._scheduled_execution = is_scheduled_run
        try:
            if close_window:
                self.Close()
            if self.combine_print:
                self._print_combined_sheets_in_order(target_sheets)
            else:
                if self.selected_doc.IsLinked:
                    self._print_linked_sheets_in_order(target_sheets, self.selected_doc)
                else:
                    self._print_sheets_in_order(target_sheets)
        finally:
            self._scheduled_execution = prev_scheduled_execution

    def _sync_before_scheduled_print(self):
        sync_doc = self.selected_doc if self.selected_doc else revit.doc
        if sync_doc is None:
            return True

        try:
            if hasattr(sync_doc, 'IsLinked') and sync_doc.IsLinked:
                return True
        except Exception:
            pass

        try:
            if not hasattr(sync_doc, 'IsWorkshared') or not sync_doc.IsWorkshared:
                return True
        except Exception:
            return True

        try:
            if hasattr(sync_doc, 'IsDetached') and sync_doc.IsDetached:
                return True
        except Exception:
            pass

        try:
            twc_opts = DB.TransactWithCentralOptions()
            sync_opts = DB.SynchronizeWithCentralOptions()
            rel_opts = DB.RelinquishOptions(True)
            sync_opts.SetRelinquishOptions(rel_opts)
            sync_opts.Comment = "Scheduled print pre-sync"
            sync_opts.Compact = False
            sync_doc.SynchronizeWithCentral(twc_opts, sync_opts)
            logger.info("Scheduled print pre-sync completed.")
            return True
        except Exception as ex:
            logger.error("Scheduled print pre-sync failed: %s", ex)
            return False

    def print_sheets(self, sender, args):
        target_sheets = self._get_target_sheets(ask_user=True)
        if not target_sheets:
            return
        self._run_print(target_sheets, confirm=True, close_window=True)

    def schedule_print(self, sender, args):
        target_sheets = self._get_target_sheets(ask_user=True)
        if not target_sheets:
            return
        if not self._validate_export_options():
            return
        if not self._validate_excel_path():
            return
        if self._is_csv_writeback_enabled() and not self._validate_excel_path(require_nonempty=True):
            return
        run_at = self._parse_schedule_time()
        if run_at is None:
            return
        if self._scheduler is None:
            self._scheduler = PrintScheduler(self)
        self._scheduler.set_job(ScheduledJob(run_at, target_sheets))
        self._update_schedule_status()

    def write_excel_dry_run(self, sender, args):
        target_sheets = self._get_target_sheets(ask_user=True)
        if not target_sheets:
            return
        if self._write_excel_report(target_sheets,
                                    require_writeback_opt=False,
                                    allow_create=True,
                                    recompute_names=True,
                                    show_alert=True,
                                    dry_run_mode=True):
            self._load_excel_rows()
            self.options_changed(None, None)

    def cancel_schedule(self, sender, args):
        if self._scheduler:
            self._scheduler.cancel_job()
        self._update_schedule_status()

    def _parse_schedule_time(self):
        try:
            selected_date = self.schedule_date.SelectedDate
        except Exception:
            selected_date = None
        if selected_date is None:
            forms.alert('Select a schedule date.')
            return None

        try:
            time_text = self.schedule_time_tb.Text or ''
        except Exception:
            time_text = ''
        parts = time_text.strip().split(':')
        if len(parts) < 2:
            forms.alert('Enter time in HH:MM (24h) format.')
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except Exception:
            forms.alert('Enter time in HH:MM (24h) format.')
            return None

        run_at = DateTime(selected_date.Year, selected_date.Month, selected_date.Day, hour, minute, 0)
        if run_at <= DateTime.Now.AddMinutes(1):
            forms.alert('Scheduled time must be in the future.')
            return None
        return run_at

    def _update_schedule_status(self):
        try:
            if self.schedule_status_tb:
                if self._scheduler and self._scheduler.has_job:
                    job = self._scheduler.current_job
                    self.schedule_status_tb.Text = "Scheduled for {}".format(job.RunAt.ToString("yyyy-MM-dd HH:mm"))
                else:
                    self.schedule_status_tb.Text = "No schedule"
        except Exception:
            pass

    def _set_schedule_status_text(self, message):
        try:
            if self.schedule_status_tb:
                self.schedule_status_tb.Text = message
        except Exception:
            pass

    def _update_excel_report(self, target_sheets):
        self._write_excel_report(target_sheets,
                                 require_writeback_opt=True,
                                 allow_create=False,
                                 recompute_names=False,
                                 show_alert=False)

    def _collect_revit_sheets(self, target_sheets):
        revit_sheets = []
        seen_ids = set()
        source = target_sheets or []

        for item in source:
            revit_sheet = None
            try:
                if hasattr(item, 'revit_sheet') and item.revit_sheet:
                    revit_sheet = item.revit_sheet
                elif hasattr(item, 'SheetNumber') and hasattr(item, 'Name'):
                    revit_sheet = item
            except Exception:
                revit_sheet = None

            if not revit_sheet:
                continue

            try:
                sid = str(get_elementid_value(revit_sheet.Id))
            except Exception:
                sid = str(id(revit_sheet))

            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            revit_sheets.append(revit_sheet)

        if not revit_sheets:
            try:
                fallback = [x.revit_sheet for x in (self.sheet_list or [])
                            if hasattr(x, 'revit_sheet') and x.revit_sheet]
            except Exception:
                fallback = []
            for revit_sheet in fallback:
                try:
                    sid = str(get_elementid_value(revit_sheet.Id))
                except Exception:
                    sid = str(id(revit_sheet))
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                revit_sheets.append(revit_sheet)

        return revit_sheets

    def _write_excel_report(self, target_sheets,
                            require_writeback_opt=True,
                            allow_create=False,
                            recompute_names=False,
                            show_alert=False,
                            dry_run_mode=False):
        if not target_sheets:
            return False

        if require_writeback_opt:
            try:
                if hasattr(self, 'excel_writeback_cb') and self.excel_writeback_cb:
                    if not self.excel_writeback_cb.IsChecked:
                        return False
            except Exception:
                pass

        try:
            path = self.excel_path_tb.Text
        except Exception:
            path = ''

        path = self._normalize_excel_path(path or '')
        if not path and allow_create:
            path = self._save_excel_file_path(path)
            path = self._normalize_excel_path(path)

        if not path:
            if show_alert:
                forms.alert("Pick a CSV file path first.")
            return False

        parent_dir = op.dirname(path)
        if parent_dir and not op.exists(parent_dir):
            os.makedirs(parent_dir)

        resolved_path = self._resolve_excel_path(path)
        if resolved_path:
            path = resolved_path
        elif not allow_create:
            return False

        try:
            self.excel_path_tb.Text = path
        except Exception:
            pass

        if op.exists(path):
            self._load_excel_rows()
        else:
            # Prevent options refresh from trying to load a file that
            # is about to be created by this write operation.
            self._excel_path = path
            self._excel_rows_by_name = {}
            self._excel_rows_by_number = {}

        if recompute_names:
            # Dry run should be based on current options, not existing CSV/Excel
            # override names from previous runs.
            backup_name_rows = self._excel_rows_by_name
            backup_number_rows = self._excel_rows_by_number
            try:
                self._excel_rows_by_name = {}
                self._excel_rows_by_number = {}
                self.options_changed(None, None)
            finally:
                self._excel_rows_by_name = backup_name_rows
                self._excel_rows_by_number = backup_number_rows

        is_csv_target = ExcelDatabase._is_csv_path(path)
        include_ext = False
        try:
            if hasattr(self, 'csv_include_ext_cb') and self.csv_include_ext_cb:
                include_ext = bool(self.csv_include_ext_cb.IsChecked)
        except Exception:
            include_ext = False

        name_map = {}
        number_map = {}
        for sheet in target_sheets:
            try:
                if not sheet.print_filename:
                    continue
                sheet_name_key = normalize_match_text(sheet.name)
                sheet_number_key = normalize_match_text(sheet.number)
                base_name = normalize_match_text(op.splitext(sheet.print_filename)[0])
                if include_ext:
                    pdf_enabled = bool(self.export_pdf_enabled)
                    dwg_enabled = bool(self.export_dwg_enabled)
                    if pdf_enabled and dwg_enabled and is_csv_target:
                        file_name_value = [base_name + ".pdf", base_name + ".dwg"]
                    elif pdf_enabled and dwg_enabled:
                        file_name_value = "{}.pdf;{}.dwg".format(base_name, base_name)
                    elif dwg_enabled:
                        file_name_value = base_name + ".dwg"
                    else:
                        file_name_value = base_name + ".pdf"
                else:
                    file_name_value = base_name
                name_map[sheet_name_key] = file_name_value
                if sheet_number_key:
                    number_map[sheet_number_key] = file_name_value
            except Exception:
                continue

        sheets = self._collect_revit_sheets(target_sheets)
        if not sheets:
            if show_alert:
                forms.alert("No sheets available to write to CSV.")
            return False

        try:
            path = op.abspath(ExcelDatabase.generate_or_update(
                path, sheets, name_map=name_map, number_map=number_map, force_update=True))
            try:
                self.excel_path_tb.Text = path
            except Exception:
                pass
            if show_alert:
                if dry_run_mode:
                    forms.alert("CSV updated (dry run, no printing):\n{}\nRows written: {}".format(path, len(sheets)), ok=True)
                else:
                    forms.alert("CSV updated:\n{}\nRows written: {}".format(path, len(sheets)), ok=True)
            return True
        except Exception as ex:
            if show_alert:
                forms.alert("CSV update failed:\n{}\n{}".format(path, ex))
            else:
                logger.error("Failed to update Excel report: %s", ex)
            return False

    def window_closing(self, sender, args):
        if self._scheduler and self._scheduler.has_job:
            if not forms.alert('A scheduled print is pending. Cancel schedule and close?',
                               ok=False, yes=True, no=True):
                args.Cancel = True
                return
            self._scheduler.cancel_job()
        if self._scheduler:
            self._scheduler.shutdown()


class ScheduledJob(object):
    def __init__(self, run_at, target_sheets):
        self.RunAt = run_at
        self.TargetSheets = list(target_sheets) if target_sheets else []
        self.IsRunning = False
        self.RemindersShown = set()


class PrintScheduler(object):
    def __init__(self, window):
        self._window = window
        self._job = None
        self._uiapp = revit.uidoc.Application if revit.uidoc else None
        self._handler = self.on_idling
        self._timer = None
        if self._uiapp:
            self._uiapp.Idling += self._handler
        try:
            self._timer = Windows.Threading.DispatcherTimer()
            self._timer.Interval = TimeSpan.FromSeconds(1)
            self._timer.Tick += self.on_timer_tick
            self._timer.Start()
        except Exception as ex:
            logger.warning("Failed to start schedule timer fallback: %s", ex)

    @property
    def has_job(self):
        return self._job is not None

    @property
    def current_job(self):
        return self._job

    def set_job(self, job):
        self._job = job

    def cancel_job(self):
        self._job = None

    def _get_due_reminder_mark(self, job, remaining_seconds):
        if remaining_seconds <= 0:
            return None

        remaining_int = int(remaining_seconds)
        if remaining_int <= 0:
            return None

        # 10-second countdown in final 10 seconds.
        if remaining_int <= 10:
            return remaining_int if remaining_int not in job.RemindersShown else None

        # Every 10 seconds once under 60 seconds (60, 50, 40, 30, 20).
        if remaining_int <= 60:
            mark = int(((remaining_int + 9) // 10) * 10)
            if mark < 20:
                mark = 20
            if mark > 60:
                mark = 60
            return mark if mark not in job.RemindersShown else None

        # One-time 5-minute reminder.
        if remaining_int <= 300 and 300 not in job.RemindersShown:
            return 300

        return None

    def _show_schedule_reminder(self, mark):
        if mark == 300:
            msg = "Scheduled print starts in 5 minutes."
        elif mark >= 20:
            msg = "Scheduled print starts in {} seconds.".format(mark)
        else:
            msg = "Scheduled print starts in {} second{}.".format(
                mark, '' if mark == 1 else 's')

        try:
            self._window._set_schedule_status_text(msg)
            logger.info(msg)
            return True
        except Exception as ex:
            logger.warning("Schedule reminder update failed: %s", ex)
            return True

    def _handle_reminders(self, job):
        try:
            remaining_seconds = (job.RunAt - DateTime.Now).TotalSeconds
            mark = self._get_due_reminder_mark(job, remaining_seconds)
            if not mark:
                return True

            self._show_schedule_reminder(mark)
            job.RemindersShown.add(mark)
            return True
        except Exception as ex:
            logger.warning("Schedule reminder handling failed: %s", ex)
            # Continue schedule even if reminder logic fails.
            return True

    def shutdown(self):
        if self._timer:
            try:
                self._timer.Stop()
                self._timer.Tick -= self.on_timer_tick
            except Exception:
                pass
        if self._uiapp:
            try:
                self._uiapp.Idling -= self._handler
            except Exception:
                pass

    def _process_schedule(self):
        try:
            if self._job is None or self._job.IsRunning:
                return
            if DateTime.Now < self._job.RunAt:
                self._handle_reminders(self._job)
                return
            self._job.IsRunning = True
            job = self._job
            self._job = None
            self._window._set_schedule_status_text("Running scheduled print...")
            try:
                prev_suppress = self._window._suppress_csv_popups
                self._window._suppress_csv_popups = True
                self._window._run_print(job.TargetSheets, confirm=False, close_window=False)
            except Exception as ex:
                logger.error("Scheduled print failed: %s", ex)
            finally:
                try:
                    self._window._suppress_csv_popups = prev_suppress
                except Exception:
                    self._window._suppress_csv_popups = False
                self._window._update_schedule_status()
        except Exception as ex:
            logger.error("Schedule idling handler failed: %s", ex)

    def on_idling(self, sender, args):
        self._process_schedule()

    def on_timer_tick(self, sender, args):
        self._process_schedule()


def cleanup_sheetnumbers(doc):
    sheets = revit.query.get_sheets(doc=doc)
    with revit.Transaction('Cleanup Sheet Numbers', doc=doc):
        for sheet in sheets:
            sheet.SheetNumber = sheet.SheetNumber.replace(NPC, '')


# verify model is printable
forms.check_modeldoc(exitscript=True)
# ensure there is nothing selected
revit.selection.get_selection().clear()

# TODO: add copy filenames to sheet list
if __shiftclick__:  #pylint: disable=E0602
    open_docs = forms.select_open_docs(check_more_than_one=False)
    if open_docs:
        for open_doc in open_docs:
            cleanup_sheetnumbers(open_doc)
else:
    PrintSheetsWindow('PrintSheets.xaml').ShowDialog()
