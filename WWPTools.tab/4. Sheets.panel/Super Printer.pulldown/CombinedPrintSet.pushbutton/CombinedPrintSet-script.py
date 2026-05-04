#!python3
# -*- coding: utf-8 -*-

from System import Int64
import ast
import os
import shutil
import sys
import tempfile
import time
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Drawing")

from Autodesk.Revit import DB

from System.Drawing.Printing import PrinterSettings
from System.IO import File
from System.Windows import RoutedEventHandler
from System.Windows.Controls import ListBoxItem, SelectionChangedEventHandler, TextChangedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from WWP_settings import get_tool_settings
import WWP_uiUtils as ui
from WWP_versioning import apply_window_title


uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
app = __revit__.Application
config, save_config = get_tool_settings("CombinedPrintSet", doc=doc)

CONFIG_LAST_OUTPUT_DIR = "last_output_dir"
CONFIG_LAST_PRINTER = "last_printer"
CONFIG_LAST_SOURCE_DIR = "last_source_dir"

PREVIEW_OUTPUT_NAME = "Combined Drawing Set.pdf"
WAIT_TIMEOUT_SECONDS = 75.0
WAIT_STABLE_POLLS = 3
WAIT_FILE_APPEAR_SECONDS = 20.0
WAIT_ZERO_BYTE_ABORT_SECONDS = 15.0
WAIT_NO_GROWTH_SECONDS = 20.0
_WPFUI_THEME_READY = False
UNSUPPORTED_SILENT_PDF_PRINTERS = (
    "microsoft print to pdf",
)




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def _read_bundle_title():
    bundle_path = os.path.join(script_dir, "bundle.yaml")
    if not os.path.isfile(bundle_path):
        return "Combined Print Set"
    try:
        with open(bundle_path, "r") as bundle_file:
            for raw_line in bundle_file:
                line = raw_line.strip()
                if not line.lower().startswith("title:"):
                    continue
                value = line.split(":", 1)[1].strip()
                if not value:
                    break
                try:
                    parsed = ast.literal_eval(value)
                    if parsed:
                        return str(parsed)
                except Exception:
                    return value.strip("\"'")
    except Exception:
        pass
    return "Combined Print Set"


BUNDLE_TITLE = _read_bundle_title()
WINDOW_TITLE = " ".join(BUNDLE_TITLE.splitlines()).strip() or "Combined Print Set"


def ensure_wpfui_theme():
    global _WPFUI_THEME_READY
    if _WPFUI_THEME_READY:
        return
    try:
        revit_version = int(str(__revit__.Application.VersionNumber))
    except Exception:
        revit_version = None
    dll_name = "WWPTools.WpfUI.net8.0-windows.dll" if revit_version and revit_version >= 2025 else "WWPTools.WpfUI.net48.dll"
    dll_path = os.path.join(lib_path, dll_name)
    if not os.path.isfile(dll_path):
        return
    try:
        if hasattr(clr, "AddReferenceToFileAndPath"):
            clr.AddReferenceToFileAndPath(dll_path)
        else:
            clr.AddReference(dll_path)
        _WPFUI_THEME_READY = True
    except Exception:
        pass


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _element_id_value(elem_id):
    if elem_id is None:
        return None
    if hasattr(elem_id, "IntegerValue"):
        return _elem_id_int(elem_id)
    if hasattr(elem_id, "Value"):
        return elem_id.Value
    try:
        return int(elem_id)
    except Exception:
        return None


def _normalize_text(value):
    return " ".join(str(value or "").split()).strip()


def _normalize_path(path):
    try:
        if not path:
            return ""
        return os.path.normcase(os.path.normpath(path))
    except Exception:
        return path or ""


def _sanitize_file_name(value):
    text = str(value or "").strip()
    invalid = '<>:"/\\|?*'
    for char in invalid:
        text = text.replace(char, "_")
    text = " ".join(text.split())
    return text or "output"


def _doc_path(current_doc):
    try:
        return current_doc.PathName or ""
    except Exception:
        return ""


def _doc_title(current_doc):
    try:
        return _normalize_text(current_doc.Title)
    except Exception:
        return "Untitled"


def _source_label_for_doc(current_doc, path_override=None):
    path_value = path_override or _doc_path(current_doc)
    if path_value:
        return os.path.splitext(os.path.basename(path_value))[0]
    return _doc_title(current_doc) or "Unsaved Model"


def _source_key_for_doc(current_doc, path_override=None):
    normalized = _normalize_path(path_override or _doc_path(current_doc))
    if normalized:
        return "path:" + normalized
    return "session:{}:{}".format(_doc_title(current_doc), current_doc.GetHashCode())


def _is_printable_sheet(sheet):
    if sheet is None:
        return False
    try:
        if sheet.IsPlaceholder:
            return False
    except Exception:
        pass
    try:
        if not sheet.CanBePrinted:
            return False
    except Exception:
        pass
    return True


def _sheet_sort_key(sheet):
    return (_normalize_text(getattr(sheet, "SheetNumber", "")).lower(), _normalize_text(getattr(sheet, "Name", "")).lower())


def _sheet_display(sheet_number, sheet_name):
    number = _normalize_text(sheet_number) or "(no number)"
    name = _normalize_text(sheet_name) or "(no name)"
    return "{} - {}".format(number, name)


def _collect_sheet_entries(current_doc, source_key, source_label):
    items = []
    collector = DB.FilteredElementCollector(current_doc).OfClass(DB.ViewSheet).ToElements()
    for sheet in sorted(collector, key=_sheet_sort_key):
        if not _is_printable_sheet(sheet):
            continue
        entry = {
            "source_key": source_key,
            "source_label": source_label,
            "sheet_id": _element_id_value(sheet.Id),
            "sheet_number": _normalize_text(getattr(sheet, "SheetNumber", "")),
            "sheet_name": _normalize_text(getattr(sheet, "Name", "")),
        }
        entry["display"] = _sheet_display(entry["sheet_number"], entry["sheet_name"])
        entry["print_set_display"] = "{} | {}".format(source_label, entry["display"])
        items.append(entry)
    return items


def _is_supported_project_document(current_doc):
    if current_doc is None:
        return False
    try:
        if current_doc.IsFamilyDocument:
            return False
    except Exception:
        pass
    try:
        if current_doc.IsLinked:
            return False
    except Exception:
        pass
    return True


def _build_source_record(current_doc, origin, path_override=None):
    if not _is_supported_project_document(current_doc):
        return None
    source_key = _source_key_for_doc(current_doc, path_override=path_override)
    source_label = _source_label_for_doc(current_doc, path_override=path_override)
    sheet_entries = _collect_sheet_entries(current_doc, source_key, source_label)
    if not sheet_entries:
        return None
    path_value = path_override or _doc_path(current_doc)
    record = {
        "key": source_key,
        "origin": origin,
        "label": source_label,
        "path": path_value or "",
        "session_doc": current_doc if origin == "open" else None,
        "sheet_items": sheet_entries,
    }
    record["list_label"] = "{}  ({})".format(source_label, len(sheet_entries))
    return record


def _collect_open_source_records():
    records = []
    seen_keys = set()
    try:
        documents = list(app.Documents)
    except Exception:
        documents = []
    for current_doc in documents:
        record = _build_source_record(current_doc, origin="open")
        if record is None or record["key"] in seen_keys:
            continue
        seen_keys.add(record["key"])
        records.append(record)
    records.sort(key=lambda item: item["label"].lower())
    return records


def _open_document_for_path(path_value):
    model_path = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path_value)
    options = DB.OpenOptions()
    return app.OpenDocumentFile(model_path, options)


def _load_source_record_from_path(path_value):
    temporary_doc = None
    try:
        temporary_doc = _open_document_for_path(path_value)
        return _build_source_record(temporary_doc, origin="file", path_override=path_value)
    finally:
        if temporary_doc is not None:
            try:
                temporary_doc.Close(False)
            except Exception:
                pass


def _default_output_path():
    configured_dir = _normalize_path(getattr(config, CONFIG_LAST_OUTPUT_DIR, "") or "")
    if configured_dir and os.path.isdir(configured_dir):
        return os.path.join(configured_dir, PREVIEW_OUTPUT_NAME)
    try:
        if doc and _doc_path(doc):
            folder = os.path.dirname(_doc_path(doc))
            if folder and os.path.isdir(folder):
                return os.path.join(folder, PREVIEW_OUTPUT_NAME)
    except Exception:
        pass
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(desktop):
        return os.path.join(desktop, PREVIEW_OUTPUT_NAME)
    return os.path.join(tempfile.gettempdir(), PREVIEW_OUTPUT_NAME)


def _is_pdf_printer(printer_name):
    return "pdf" in str(printer_name or "").lower()


def _requires_interactive_save_dialog(printer_name):
    return str(printer_name or "").strip().lower() in UNSUPPORTED_SILENT_PDF_PRINTERS


def _unsupported_printer_message(printer_name):
    return (
        "The selected printer '{}' is not supported by Combined Print Set because it can ignore the configured "
        "output path and wait on a hidden Save dialog, which leaves Revit hanging and produces 0 KB PDFs.\n\n"
        "Use a PDF printer that supports unattended PrintToFile output."
    ).format(printer_name)


def _collect_printers():
    names = []
    try:
        for printer_name in PrinterSettings.InstalledPrinters:
            names.append(str(printer_name))
    except Exception:
        pass
    unique_names = []
    seen = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_names.append(name)
    unique_names.sort(
        key=lambda item: (
            0 if _is_pdf_printer(item) and not _requires_interactive_save_dialog(item) else
            1 if _is_pdf_printer(item) else
            2,
            item.lower(),
        )
    )
    return unique_names


def _is_valid_pdf_file(path_value):
    try:
        if not os.path.isfile(path_value):
            return False
        if os.path.getsize(path_value) <= 4:
            return False
        with open(path_value, "rb") as pdf_stream:
            return pdf_stream.read(5) == b"%PDF-"
    except Exception:
        return False


def _wait_for_pdf(path_value, timeout_seconds=WAIT_TIMEOUT_SECONDS):
    start_time = time.time()
    deadline = time.time() + float(timeout_seconds)
    last_seen_time = None
    last_growth_time = None
    last_size = -1
    stable_polls = 0
    while time.time() <= deadline:
        now = time.time()
        if os.path.isfile(path_value):
            try:
                current_size = os.path.getsize(path_value)
            except Exception:
                current_size = -1
            if last_seen_time is None:
                last_seen_time = now
            if current_size > 0:
                if current_size == last_size:
                    stable_polls += 1
                    if stable_polls >= WAIT_STABLE_POLLS:
                        if _is_valid_pdf_file(path_value):
                            return True
                        raise Exception("The PDF printer created a file, but it is not a valid PDF: {}".format(path_value))
                else:
                    last_size = current_size
                    last_growth_time = now
                    stable_polls = 0
            else:
                if last_seen_time is not None and (now - last_seen_time) >= WAIT_ZERO_BYTE_ABORT_SECONDS:
                    raise Exception(
                        "The PDF printer created a 0 KB file and never wrote PDF data.\n"
                        "This usually means the printer is waiting on a hidden Save dialog or does not support silent PrintToFile output.\n"
                        "File: {}".format(path_value)
                    )
            if last_growth_time is not None and current_size > 0 and (now - last_growth_time) >= WAIT_NO_GROWTH_SECONDS:
                if _is_valid_pdf_file(path_value):
                    return True
                raise Exception("The PDF file stopped growing before a valid PDF was produced: {}".format(path_value))
        elif (now - start_time) >= WAIT_FILE_APPEAR_SECONDS:
            raise Exception(
                "The PDF printer did not create the output file within {} seconds.\n"
                "This usually means the printer ignored PrintToFile or is waiting on an interactive prompt.\n"
                "Expected file: {}".format(int(WAIT_FILE_APPEAR_SECONDS), path_value)
            )
        time.sleep(0.5)
    raise Exception("Timed out waiting for the PDF printer to finish writing:\n{}".format(path_value))


def _merge_pdf_files(input_paths, output_path):
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception as exc:
        raise Exception("The bundled pypdf library is unavailable: {}".format(exc))

    writer = PdfWriter()
    try:
        for input_path in input_paths:
            reader = PdfReader(input_path)
            for page in reader.pages:
                writer.add_page(page)
        folder = os.path.dirname(output_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(output_path, "wb") as target_stream:
            writer.write(target_stream)
    finally:
        try:
            writer.close()
        except Exception:
            pass


def _configure_print_manager(print_manager, printer_name, output_path):
    if _requires_interactive_save_dialog(printer_name):
        raise Exception(_unsupported_printer_message(printer_name))
    print_manager.SelectNewPrintDriver(printer_name)
    print_manager.PrintToFile = True
    try:
        print_manager.CombinedFile = True
    except Exception:
        pass
    print_manager.PrintToFileName = output_path
    try:
        print_manager.PrintRange = DB.PrintRange.Select
    except Exception:
        pass
    print_manager.Apply()


def _try_native_pdf_export(current_doc, sheet, output_path):
    """
    Export using Revit's built-in PDF API (Revit 2022+).
    Uses .NET reflection to bypass IronPython overload-resolution type mismatches.
    Returns False only when the API does not exist (pre-2022 Revit).
    Returns True on success.
    Raises Exception with a descriptive message on any other failure.
    """
    import System
    from System.Collections.Generic import List as NetList

    # Use .GetType() (a .NET instance method) to get the true CLR System.Type.
    # type() in IronPython returns the IronPython wrapper which has no .Assembly.
    doc_clr_type = current_doc.GetType()
    pdf_opts_type = doc_clr_type.Assembly.GetType("Autodesk.Revit.DB.PDFExportOptions")
    if pdf_opts_type is None:
        return False  # Pre-Revit 2022 — fall through to virtual printer

    # Find the PDF export overload with the expected 3-parameter signature:
    # Export(string folder, IList<ElementId> viewIds, PDFExportOptions options)
    export_method = None
    export_overloads_found = []
    for m in doc_clr_type.GetMethods():
        if m.Name != "Export":
            continue
        params = m.GetParameters()
        param_names = []
        for p in params:
            try:
                param_names.append(p.ParameterType.Name)
            except Exception:
                param_names.append("?")
        export_overloads_found.append("Export({})".format(", ".join(param_names)))
        if len(params) != 3:
            continue
        try:
            if params[0].ParameterType.FullName != "System.String":
                continue
            if "IList" not in params[1].ParameterType.Name:
                continue
            if "PDFExportOptions" not in params[2].ParameterType.FullName:
                continue
        except Exception:
            continue
        export_method = m
        break
    if export_method is None:
        raise Exception(
            "PDF export method not found. Available Export overloads on {}:\n{}".format(
                doc_clr_type.Name,
                "\n".join(export_overloads_found) if export_overloads_found else "(none)"
            )
        )

    # Create options via Activator so the instance comes from the same assembly
    options = System.Activator.CreateInstance(pdf_opts_type)
    combine_prop = pdf_opts_type.GetProperty("Combine")
    if combine_prop is not None:
        combine_prop.SetValue(options, False)
    file_name_prop = pdf_opts_type.GetProperty("FileName")
    if file_name_prop is not None:
        file_name_prop.SetValue(options, _sanitize_file_name("{}_{}".format(sheet.SheetNumber, sheet.Name)))

    # Build List<ElementId> — use DB.ElementId directly (simpler, no type-identity issue here)
    id_list = NetList[DB.ElementId]()
    id_list.Add(sheet.Id)

    temp_export_dir = tempfile.mkdtemp(prefix="WWPTools_NativePDF_")
    try:
        before = set(os.listdir(temp_export_dir))

        # Invoke via MethodInfo.Invoke — bypasses IronPython type-system entirely
        invoke_args = System.Array[System.Object]([temp_export_dir, id_list, options])
        result = export_method.Invoke(current_doc, invoke_args)

        # Return type is IList<string> (Revit 2022) or bool (some later versions)
        if result is None:
            raise Exception("Revit's built-in PDF export returned None.")
        try:
            if hasattr(result, "Count") and result.Count == 0:
                raise Exception("Revit's built-in PDF export returned an empty file list.")
        except AttributeError:
            if not result:
                raise Exception("Revit's built-in PDF export returned False.")

        new_pdfs = [
            f for f in os.listdir(temp_export_dir)
            if f not in before and f.lower().endswith(".pdf")
        ]
        if not new_pdfs:
            raise Exception(
                "Revit's built-in PDF export reported success but created no PDF file.\n"
                "Temp folder: {}".format(temp_export_dir)
            )

        exported = os.path.join(temp_export_dir, new_pdfs[0])
        if not _is_valid_pdf_file(exported):
            raise Exception(
                "Revit's built-in PDF export produced an invalid PDF:\n{}".format(exported)
            )

        dest_folder = os.path.dirname(output_path)
        if dest_folder and not os.path.isdir(dest_folder):
            os.makedirs(dest_folder)
        shutil.move(exported, output_path)
        return True
    finally:
        shutil.rmtree(temp_export_dir, ignore_errors=True)


def _print_sheet_to_pdf(current_doc, sheet, printer_name, output_path):
    if os.path.isfile(output_path):
        try:
            os.remove(output_path)
        except Exception:
            pass

    # Try Revit's built-in PDF export (Revit 2022+, no external printer needed).
    # Returns False only on pre-2022 Revit; raises with a clear message on any failure.
    try:
        if _try_native_pdf_export(current_doc, sheet, output_path):
            return
    except Exception as exc:
        raise Exception("Failed to print '{}': {}".format(
            _sheet_display(sheet.SheetNumber, sheet.Name), exc))

    # Pre-2022 Revit only: fall back to virtual PDF printer
    last_error = None
    for _ in range(2):
        try:
            _configure_print_manager(current_doc.PrintManager, printer_name, output_path)
            success = current_doc.PrintManager.SubmitPrint(sheet)
            if not success:
                raise Exception("The printer reported an unsuccessful print submission.")
            _wait_for_pdf(output_path)
            if not _is_valid_pdf_file(output_path):
                raise Exception("The printer finished, but the output is not a valid PDF:\n{}".format(output_path))
            return
        except Exception as exc:
            last_error = exc
            try:
                if os.path.isfile(output_path):
                    os.remove(output_path)
            except Exception:
                pass
            time.sleep(1.0)
    raise Exception("Failed to print '{}': {}".format(_sheet_display(sheet.SheetNumber, sheet.Name), last_error))


def _ensure_output_path(path_value):
    output_path = str(path_value or "").strip()
    if not output_path:
        raise Exception("Choose an output PDF path.")
    folder = os.path.dirname(output_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if os.path.isdir(output_path):
        raise Exception("Output path points to a folder, not a PDF file.")
    if not output_path.lower().endswith(".pdf"):
        output_path += ".pdf"
    return output_path


def _existing_open_docs_by_key():
    result = {}
    try:
        documents = list(app.Documents)
    except Exception:
        documents = []
    for current_doc in documents:
        if not _is_supported_project_document(current_doc):
            continue
        result[_source_key_for_doc(current_doc)] = current_doc
        path_value = _normalize_path(_doc_path(current_doc))
        if path_value:
            result["path:" + path_value] = current_doc
    return result


def _open_required_documents(source_lookup, selected_items):
    docs_by_key = {}
    opened_docs = []
    available_open_docs = _existing_open_docs_by_key()
    for item in selected_items:
        source_key = item["source_key"]
        if source_key in docs_by_key:
            continue
        source = source_lookup.get(source_key)
        if source is None:
            raise Exception("Source model is no longer available for '{}'.".format(item["print_set_display"]))
        if source_key in available_open_docs:
            docs_by_key[source_key] = available_open_docs[source_key]
            continue
        if source.get("session_doc") is not None:
            docs_by_key[source_key] = source["session_doc"]
            continue
        path_value = source.get("path") or ""
        if not path_value or not os.path.isfile(path_value):
            raise Exception("Source file was not found:\n{}".format(path_value or source.get("label", "")))
        opened_doc = _open_document_for_path(path_value)
        docs_by_key[source_key] = opened_doc
        opened_docs.append(opened_doc)
    return docs_by_key, opened_docs


def _sheet_from_entry(current_doc, entry):
    sheet_id = entry.get("sheet_id")
    if sheet_id is None:
        return None
    try:
        return current_doc.GetElement(DB.ElementId(Int64(int(sheet_id))))
    except Exception:
        return None


def _temp_pdf_name(index_value, entry):
    label = "{}_{}_{}".format(
        str(index_value).zfill(3),
        entry.get("sheet_number", "") or "sheet",
        entry.get("sheet_name", "") or "view",
    )
    return _sanitize_file_name(label) + ".pdf"


def _print_combined_pdf(source_lookup, selected_items, printer_name, output_path):
    output_path = _ensure_output_path(output_path)
    docs_by_key, opened_docs = _open_required_documents(source_lookup, selected_items)
    temp_dir = tempfile.mkdtemp(prefix="WWPTools_CombinedPrint_")
    temp_paths = []
    try:
        for index_value, entry in enumerate(selected_items, 1):
            current_doc = docs_by_key[entry["source_key"]]
            sheet = _sheet_from_entry(current_doc, entry)
            if not isinstance(sheet, DB.ViewSheet) or not _is_printable_sheet(sheet):
                raise Exception("Sheet is no longer printable: {}".format(entry["print_set_display"]))
            temp_path = os.path.join(temp_dir, _temp_pdf_name(index_value, entry))
            _print_sheet_to_pdf(current_doc, sheet, printer_name, temp_path)
            temp_paths.append(temp_path)
        _merge_pdf_files(temp_paths, output_path)
        return output_path
    finally:
        for opened_doc in opened_docs:
            try:
                opened_doc.Close(False)
            except Exception:
                pass
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


class CombinedPrintSetDialog(object):
    def __init__(self, source_records, printers):
        self.source_records = list(source_records or [])
        self.printers = list(printers or [])
        self.result = None
        self._source_lookup = {}
        self._selected_entries = []
        self._available_entries = []
        self._selected_source_key = None

        ensure_wpfui_theme()
        xaml_path = os.path.join(script_dir, "CombinedPrintSetWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._btn_add_files = self.window.FindName("BtnAddFiles")
        self._btn_remove_source = self.window.FindName("BtnRemoveSource")
        self._sources_list = self.window.FindName("SourcesList")
        self._txt_sheet_filter = self.window.FindName("TxtSheetFilter")
        self._available_sheets = self.window.FindName("AvailableSheetsList")
        self._btn_add_selected = self.window.FindName("BtnAddSelected")
        self._btn_add_all = self.window.FindName("BtnAddAll")
        self._selected_sheets = self.window.FindName("SelectedSheetsList")
        self._btn_move_top = self.window.FindName("BtnMoveTop")
        self._btn_move_up = self.window.FindName("BtnMoveUp")
        self._btn_move_down = self.window.FindName("BtnMoveDown")
        self._btn_move_bottom = self.window.FindName("BtnMoveBottom")
        self._btn_remove_selected = self.window.FindName("BtnRemoveSelected")
        self._btn_clear_set = self.window.FindName("BtnClearSet")
        self._cmb_printer = self.window.FindName("CmbPrinter")
        self._txt_output_path = self.window.FindName("TxtOutputPath")
        self._btn_browse_output = self.window.FindName("BtnBrowseOutput")
        self._txt_summary = self.window.FindName("TxtSummary")
        self._txt_warning = self.window.FindName("TxtWarning")
        self._footer_status = self.window.FindName("FooterStatus")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._btn_print = self.window.FindName("BtnPrint")

        self._header_title.Text = WINDOW_TITLE
        self._header_subtitle.Text = "Load sheets from open or closed Revit models, arrange the final order, and print one merged PDF drawing set."
        self._txt_sheet_filter.Text = ""
        self._txt_output_path.Text = _default_output_path()

        self._bind_events()
        self._load_sources(self.source_records)
        self._load_printers()
        self._update_summary()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _bind_events(self):
        self._btn_add_files.Click += RoutedEventHandler(self._on_add_files)
        self._btn_remove_source.Click += RoutedEventHandler(self._on_remove_source)
        self._sources_list.SelectionChanged += SelectionChangedEventHandler(self._on_source_changed)
        self._txt_sheet_filter.TextChanged += TextChangedEventHandler(self._on_filter_changed)
        self._btn_add_selected.Click += RoutedEventHandler(self._on_add_selected)
        self._btn_add_all.Click += RoutedEventHandler(self._on_add_all)
        self._btn_move_top.Click += RoutedEventHandler(self._on_move_top)
        self._btn_move_up.Click += RoutedEventHandler(self._on_move_up)
        self._btn_move_down.Click += RoutedEventHandler(self._on_move_down)
        self._btn_move_bottom.Click += RoutedEventHandler(self._on_move_bottom)
        self._btn_remove_selected.Click += RoutedEventHandler(self._on_remove_selected)
        self._btn_clear_set.Click += RoutedEventHandler(self._on_clear_set)
        self._btn_browse_output.Click += RoutedEventHandler(self._on_browse_output)
        self._btn_cancel.Click += RoutedEventHandler(self._on_cancel)
        self._btn_print.Click += RoutedEventHandler(self._on_print)

    def _load_sources(self, records):
        self._source_lookup = {}
        self._sources_list.Items.Clear()
        ordered = sorted(records, key=lambda item: item["label"].lower())
        self.source_records = ordered
        for record in ordered:
            self._source_lookup[record["key"]] = record
            item = ListBoxItem()
            item.Content = record["list_label"]
            item.Tag = record["key"]
            self._sources_list.Items.Add(item)
        if self._sources_list.Items.Count > 0:
            self._sources_list.SelectedIndex = 0
        else:
            self._available_entries = []
            self._available_sheets.Items.Clear()
            self._selected_source_key = None
        self._update_summary()

    def _load_printers(self):
        self._cmb_printer.Items.Clear()
        selected_name = _normalize_text(getattr(config, CONFIG_LAST_PRINTER, "") or "")
        selected_index = -1
        for index_value, printer_name in enumerate(self.printers):
            self._cmb_printer.Items.Add(printer_name)
            if selected_name and printer_name.lower() == selected_name.lower():
                selected_index = index_value
        if selected_index < 0:
            for index_value, printer_name in enumerate(self.printers):
                if _is_pdf_printer(printer_name):
                    selected_index = index_value
                    break
        if selected_index < 0 and self._cmb_printer.Items.Count > 0:
            selected_index = 0
        if selected_index >= 0:
            self._cmb_printer.SelectedIndex = selected_index

    def _filtered_entries(self, source_record):
        if source_record is None:
            return []
        filter_text = _normalize_text(self._txt_sheet_filter.Text).lower()
        entries = list(source_record.get("sheet_items", []))
        if not filter_text:
            return entries
        return [
            entry
            for entry in entries
            if filter_text in entry["display"].lower()
            or filter_text in entry["sheet_number"].lower()
            or filter_text in entry["sheet_name"].lower()
        ]

    def _refresh_available_sheets(self):
        self._available_sheets.Items.Clear()
        source_record = self._source_lookup.get(self._selected_source_key)
        self._available_entries = self._filtered_entries(source_record)
        for entry in self._available_entries:
            item = ListBoxItem()
            item.Content = entry["display"]
            item.Tag = entry
            self._available_sheets.Items.Add(item)
        self._update_summary()

    def _refresh_selected_list(self, selected_keys=None):
        selected_keys = set(selected_keys or [])
        self._selected_sheets.Items.Clear()
        for index_value, entry in enumerate(self._selected_entries, 1):
            item = ListBoxItem()
            item.Tag = entry
            item.Content = "{}. {}".format(index_value, entry["print_set_display"])
            if entry["entry_key"] in selected_keys:
                item.IsSelected = True
            self._selected_sheets.Items.Add(item)
        self._update_summary()

    def _selected_available_entries(self):
        result = []
        for item in list(self._available_sheets.SelectedItems):
            entry = getattr(item, "Tag", None)
            if entry is not None:
                result.append(entry)
        return result

    def _selected_print_entries(self):
        result = []
        for item in list(self._selected_sheets.SelectedItems):
            entry = getattr(item, "Tag", None)
            if entry is not None:
                result.append(entry)
        return result

    def _make_selected_entry(self, entry):
        copied = dict(entry)
        copied["entry_key"] = "{}:{}:{}".format(entry["source_key"], entry["sheet_id"], len(self._selected_entries) + 1)
        return copied

    def _append_entries(self, entries):
        existing_pairs = set((entry["source_key"], entry["sheet_id"]) for entry in self._selected_entries)
        added_keys = []
        for entry in entries:
            pair = (entry["source_key"], entry["sheet_id"])
            if pair in existing_pairs:
                continue
            copied = self._make_selected_entry(entry)
            self._selected_entries.append(copied)
            existing_pairs.add(pair)
            added_keys.append(copied["entry_key"])
        self._refresh_selected_list(selected_keys=added_keys)

    def _move_selected(self, direction):
        selected = self._selected_print_entries()
        if not selected:
            return
        keys = [entry["entry_key"] for entry in selected]
        if direction == "top":
            remaining = [entry for entry in self._selected_entries if entry["entry_key"] not in keys]
            self._selected_entries = list(selected) + remaining
        elif direction == "bottom":
            remaining = [entry for entry in self._selected_entries if entry["entry_key"] not in keys]
            self._selected_entries = remaining + list(selected)
        elif direction == "up":
            selected_keys = set(keys)
            for index_value in range(1, len(self._selected_entries)):
                current = self._selected_entries[index_value]
                previous = self._selected_entries[index_value - 1]
                if current["entry_key"] in selected_keys and previous["entry_key"] not in selected_keys:
                    self._selected_entries[index_value - 1], self._selected_entries[index_value] = current, previous
        elif direction == "down":
            selected_keys = set(keys)
            for index_value in range(len(self._selected_entries) - 2, -1, -1):
                current = self._selected_entries[index_value]
                next_item = self._selected_entries[index_value + 1]
                if current["entry_key"] in selected_keys and next_item["entry_key"] not in selected_keys:
                    self._selected_entries[index_value], self._selected_entries[index_value + 1] = next_item, current
        self._refresh_selected_list(selected_keys=keys)

    def _update_summary(self):
        source_count = len(self.source_records)
        available_count = len(self._available_entries)
        set_count = len(self._selected_entries)
        self._txt_summary.Text = "{} source model(s), {} visible sheet(s), {} sheet(s) in the ordered print set.".format(
            source_count,
            available_count,
            set_count,
        )
        if set_count:
            self._footer_status.Text = "The final merged PDF follows the right-hand list order exactly."
        else:
            self._footer_status.Text = "Select sheets from a source model and add them to the ordered print set."
        self._btn_print.IsEnabled = bool(set_count and self._cmb_printer.Items.Count > 0)

    def _on_source_changed(self, sender, event_args):
        selected_item = self._sources_list.SelectedItem
        self._selected_source_key = getattr(selected_item, "Tag", None) if selected_item is not None else None
        self._refresh_available_sheets()

    def _on_filter_changed(self, sender, event_args):
        self._refresh_available_sheets()

    def _on_add_selected(self, sender, event_args):
        self._append_entries(self._selected_available_entries())

    def _on_add_all(self, sender, event_args):
        self._append_entries(self._available_entries)

    def _on_move_top(self, sender, event_args):
        self._move_selected("top")

    def _on_move_up(self, sender, event_args):
        self._move_selected("up")

    def _on_move_down(self, sender, event_args):
        self._move_selected("down")

    def _on_move_bottom(self, sender, event_args):
        self._move_selected("bottom")

    def _on_remove_selected(self, sender, event_args):
        selected_keys = set(entry["entry_key"] for entry in self._selected_print_entries())
        if not selected_keys:
            return
        self._selected_entries = [entry for entry in self._selected_entries if entry["entry_key"] not in selected_keys]
        self._refresh_selected_list()

    def _on_clear_set(self, sender, event_args):
        self._selected_entries = []
        self._refresh_selected_list()

    def _on_remove_source(self, sender, event_args):
        selected_item = self._sources_list.SelectedItem
        source_key = getattr(selected_item, "Tag", None) if selected_item is not None else None
        if not source_key:
            return
        self.source_records = [record for record in self.source_records if record["key"] != source_key]
        self._selected_entries = [entry for entry in self._selected_entries if entry["source_key"] != source_key]
        self._load_sources(self.source_records)
        self._refresh_selected_list()

    def _on_add_files(self, sender, event_args):
        initial_dir = getattr(config, CONFIG_LAST_SOURCE_DIR, "") or getattr(config, CONFIG_LAST_OUTPUT_DIR, "") or ""
        file_paths = ui.uiUtils_open_file_dialog(
            title=WINDOW_TITLE,
            filter_text="Revit Project Files (*.rvt)|*.rvt",
            multiselect=True,
            initial_directory=initial_dir,
        )
        if not file_paths:
            return

        failures = []
        added_any = False
        existing_keys = set(record["key"] for record in self.source_records)
        for path_value in file_paths:
            normalized = _normalize_path(path_value)
            if not normalized:
                continue
            if "path:" + normalized in existing_keys:
                continue
            try:
                record = _load_source_record_from_path(path_value)
                if record is None:
                    failures.append("{} (no printable sheets found)".format(path_value))
                    continue
                self.source_records.append(record)
                existing_keys.add(record["key"])
                added_any = True
            except Exception as exc:
                failures.append("{} ({})".format(path_value, exc))

        if file_paths:
            first_dir = os.path.dirname(file_paths[0])
            if first_dir:
                setattr(config, CONFIG_LAST_SOURCE_DIR, first_dir)

        if added_any:
            self._load_sources(self.source_records)
        if failures:
            ui.uiUtils_alert("Some files could not be loaded:\n\n{}".format("\n".join(failures[:10])), title=WINDOW_TITLE)

    def _on_browse_output(self, sender, event_args):
        current_path = _normalize_text(self._txt_output_path.Text)
        initial_dir = os.path.dirname(current_path) if current_path else getattr(config, CONFIG_LAST_OUTPUT_DIR, "") or ""
        file_name = os.path.basename(current_path) if current_path else PREVIEW_OUTPUT_NAME
        selected = ui.uiUtils_save_file_dialog(
            title=WINDOW_TITLE,
            filter_text="PDF Files (*.pdf)|*.pdf",
            default_extension="pdf",
            initial_directory=initial_dir,
            file_name=file_name,
        )
        if selected:
            self._txt_output_path.Text = selected

    def _on_cancel(self, sender, event_args):
        self.result = None
        self.window.DialogResult = False
        self.window.Close()

    def _selected_printer_name(self):
        item = self._cmb_printer.SelectedItem
        return str(item) if item is not None else ""

    def _on_print(self, sender, event_args):
        if not self._selected_entries:
            self._txt_warning.Text = "Add at least one sheet to the ordered print set."
            return
        printer_name = self._selected_printer_name()
        if not printer_name:
            self._txt_warning.Text = "Select a PDF printer."
            return
        if _requires_interactive_save_dialog(printer_name):
            self._txt_warning.Text = _unsupported_printer_message(printer_name)
            return
        output_path = _normalize_text(self._txt_output_path.Text)
        if not output_path:
            self._txt_warning.Text = "Choose an output PDF path."
            return
        try:
            output_path = _ensure_output_path(output_path)
        except Exception as exc:
            self._txt_warning.Text = str(exc)
            return

        self.result = {
            "source_lookup": dict(self._source_lookup),
            "selected_items": [dict(entry) for entry in self._selected_entries],
            "printer_name": printer_name,
            "output_path": output_path,
        }
        self.window.DialogResult = True
        self.window.Close()


def main():
    printers = _collect_printers()
    if not printers:
        ui.uiUtils_alert("No printers were found on this machine.", title=WINDOW_TITLE)
        return

    source_records = _collect_open_source_records()
    dialog = CombinedPrintSetDialog(source_records, printers)
    confirmed = dialog.ShowDialog()
    if not confirmed or not dialog.result:
        return

    try:
        output_path = _print_combined_pdf(
            dialog.result["source_lookup"],
            dialog.result["selected_items"],
            dialog.result["printer_name"],
            dialog.result["output_path"],
        )
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
        return

    setattr(config, CONFIG_LAST_PRINTER, dialog.result["printer_name"])
    setattr(config, CONFIG_LAST_OUTPUT_DIR, os.path.dirname(output_path))
    save_config()

    ui.uiUtils_alert(
        "Created merged PDF with {} sheet(s):\n{}".format(len(dialog.result["selected_items"]), output_path),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
