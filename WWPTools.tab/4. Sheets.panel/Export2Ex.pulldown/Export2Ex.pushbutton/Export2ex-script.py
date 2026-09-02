import csv
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import traceback

import clr
clr.AddReference('System.Xml')
from System import String
from System.Collections.Generic import List
from System.IO import File, StreamReader, StreamWriter
from System.Text import Encoding, UTF8Encoding

from pyrevit import DB
from WWP_settings import get_tool_settings
from WWP_versioning import apply_window_title



CONFIG_LAST_EXCEL_PATH = "last_excel_path"
CONFIG_LAST_CSV_DIR = "last_csv_dir"
CONFIG_LAST_SCHEDULE_IDS = "last_schedule_ids"
CONFIG_LAST_CSV_MODE = "last_csv_mode"
CONFIG_LAST_CSV_DELIM = "last_csv_delim"
CONFIG_LAST_EXPORT_MODE = "last_export_mode"
CONFIG_LAST_CSV_EXPORT_TITLE = "last_csv_export_title"
CONFIG_LAST_CSV_COLUMN_HEADERS = "last_csv_column_headers"
CONFIG_LAST_CSV_GROUP_HEADERS = "last_csv_group_headers"
CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS = "last_csv_grouped_column_headers"
CONFIG_LAST_CSV_TEXT_QUALIFIER = "last_csv_text_qualifier"
CONFIG_LAST_USE_CATEGORY_SHEET_NAME = "last_use_category_sheet_name"
LOG_FILE_NAME = "Export2Ex.log"
ALLOWED_EXCEL_EXTENSIONS = (".xlsx", ".xlsm")
PARAM_SAVED_SETS = "! P_STATS_Export_Text"
SAVED_SET_NAMESPACE = "export2ex"




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def sanitize_sheet_name(name):
    invalid = r"[:\\/?*\[\]]"
    safe = re.sub(invalid, "_", name)
    safe = safe.strip()
    if not safe:
        safe = "Schedule"
    return safe[:31]


def sanitize_file_name(name):
    invalid = r'[<>:"/\\|?*]'
    safe = re.sub(invalid, "_", name).strip()
    return safe or "Schedule"


def _pluralize(name):
    """Return a simple English plural of name (for sheet naming). Most Revit category
    names are already plural (Walls, Areas, Rooms); this handles the singular ones."""
    if not name:
        return name
    lower = name.lower()
    # Already ends in s/x/z -- treat as plural (Walls, Areas, Rooms, Stairs, ...)
    if lower[-1] in "sxz":
        return name
    # Ends in consonant + y: change to ies (e.g. "Category" -> "Categories")
    if lower.endswith("y") and len(name) > 1 and lower[-2] not in "aeiou":
        return name[:-1] + "ies"
    # Ends in ch/sh: add es
    if lower.endswith("ch") or lower.endswith("sh"):
        return name + "es"
    # Default: add s  (Parking -> Parkings, Fixture -> Fixtures, etc.)
    return name + "s"


def _get_schedule_category_name(doc, view):
    """Return the Revit category name for a schedule view, falling back to view.Name."""
    try:
        cat_id = view.Definition.CategoryId
        if cat_id is None:
            return view.Name
        try:
            cat = DB.Category.GetCategory(doc, cat_id)
            if cat is not None:
                return cat.Name
        except Exception:
            pass
        for cat in doc.Settings.Categories:
            try:
                if cat.Id.IntegerValue == cat_id.IntegerValue:
                    return cat.Name
            except Exception:
                continue
    except Exception:
        pass
    return view.Name


def normalize_excel_output_path(path, default_ext=".xlsx"):
    value = (path or "").strip()
    if not value:
        return ""
    root, ext = os.path.splitext(value)
    if not ext:
        return value + default_ext
    if ext.lower() in ALLOWED_EXCEL_EXTENSIONS:
        return value
    return ""


def _pick_save_file(title, filter_text, default_extension, initial_directory, file_name):
    clr.AddReference("PresentationFramework")
    from Microsoft.Win32 import SaveFileDialog

    dialog = SaveFileDialog()
    dialog.Title = title or "Save File"
    dialog.Filter = filter_text or "All files (*.*)|*.*"
    if default_extension:
        dialog.DefaultExt = default_extension
        dialog.AddExtension = True
    if initial_directory:
        initial_directory = os.path.expandvars(initial_directory)
    if initial_directory and os.path.isdir(initial_directory):
        dialog.InitialDirectory = initial_directory
    if file_name:
        dialog.FileName = file_name
    result = dialog.ShowDialog()
    if result:
        return dialog.FileName
    return None


def _pick_folder(title, initial_directory):
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import FolderBrowserDialog, DialogResult

    dialog = FolderBrowserDialog()
    dialog.Description = title or "Select Folder"
    if initial_directory:
        initial_directory = os.path.expandvars(initial_directory)
    if initial_directory and os.path.isdir(initial_directory):
        dialog.SelectedPath = initial_directory
    result = dialog.ShowDialog()
    if result == DialogResult.OK:
        return dialog.SelectedPath
    return None


def get_active_doc():
    """Resolve current document without importing pyrevit.revit (CPython 6.1 safety)."""
    try:
        uidoc = __revit__.ActiveUIDocument
        if uidoc:
            return uidoc.Document
    except Exception:
        pass
    return None


def read_saved_sets(doc, param_name=None):
    try:
        proj_info = doc.ProjectInformation
        if proj_info is None:
            return {}
        param = proj_info.LookupParameter(param_name or PARAM_SAVED_SETS)
        if param is None:
            return {}
        raw = (param.AsString() or "").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        if isinstance(data.get(SAVED_SET_NAMESPACE), dict):
            return data.get(SAVED_SET_NAMESPACE) or {}
        if any(k in data for k in _ALL_NAMESPACES):
            return {}
        return data
    except Exception:
        return {}


_ALL_NAMESPACES = ("export2ex", "export2ex_beta", "mass_stats")


def _looks_like_legacy_saved_sets(data):
    if not isinstance(data, dict) or not data:
        return False
    if any(k in data for k in _ALL_NAMESPACES):
        return False
    return all(isinstance(v, dict) for v in data.values())


def write_saved_sets(doc, sets_dict, param_name=None):
    try:
        proj_info = doc.ProjectInformation
        if proj_info is None:
            return False
        param = proj_info.LookupParameter(param_name or PARAM_SAVED_SETS)
        t = DB.Transaction(doc, "Save Export2Ex Settings")
        t.Start()
        try:
            if param is None:
                t.RollBack()
                return False
            raw = (param.AsString() or "").strip()
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if _looks_like_legacy_saved_sets(payload):
                payload = {SAVED_SET_NAMESPACE: payload}
            payload[SAVED_SET_NAMESPACE] = sets_dict
            param.Set(json.dumps(payload, ensure_ascii=False, indent=2))
            t.Commit()
            return True
        except Exception as inner:
            try:
                t.RollBack()
            except Exception:
                pass
            log_exception("write_saved_sets transaction", inner)
            return False
    except Exception as exc:
        log_exception("write_saved_sets", exc)
        return False


def _get_proj_info_text_params(doc):
    """Return sorted names of writable text parameters on Project Information."""
    try:
        proj_info = doc.ProjectInformation
        if proj_info is None:
            return []
        names = []
        for param in proj_info.Parameters:
            try:
                if param.StorageType == DB.StorageType.String:
                    names.append(param.Definition.Name)
            except Exception:
                continue
        return sorted(set(names))
    except Exception:
        return []


def _ensure_saved_sets_param(doc, config, ui, save_config):
    """Return the Project Information param name to use for saved sets.

    Priority: canonical param -> config-stored override -> user prompt.
    Saves the user's choice to per-project config. Returns None if unavailable.
    """
    try:
        proj_info = doc.ProjectInformation
    except Exception:
        return None
    if proj_info is None:
        return None

    if proj_info.LookupParameter(PARAM_SAVED_SETS) is not None:
        return PARAM_SAVED_SETS

    stored = config_get(config, "saved_sets_param_name", "") or ""
    if stored and proj_info.LookupParameter(stored) is not None:
        return stored

    text_params = _get_proj_info_text_params(doc)
    if not text_params:
        ui.uiUtils_alert(
            "Parameter '{}' was not found in Project Information and no other "
            "writable text parameters exist.\n\n"
            "Add a text parameter to Project Information to enable saved sets.".format(PARAM_SAVED_SETS),
            title="Export Schedules - Saved Sets",
        )
        return None

    indices = ui.uiUtils_select_indices(
        text_params,
        title="Select Parameter for Saved Sets",
        prompt=(
            "'{}' was not found in this project's Project Information.\n\n"
            "Select an existing text parameter to store saved sets:"
        ).format(PARAM_SAVED_SETS),
        multiselect=False,
    )
    if not indices:
        return None

    chosen = text_params[indices[0]]
    config.saved_sets_param_name = chosen
    if save_config:
        save_config()
    return chosen


def _normalize_namespace_data(data):
    if not isinstance(data, dict):
        return {}
    if "settings" in data or "sets" in data:
        return data
    if _looks_like_legacy_saved_sets(data):
        return {"sets": data}
    return {}


class _LocalConfig(object):
    def __init__(self, file_path, data):
        object.__setattr__(self, "_file_path", file_path)
        object.__setattr__(self, "_data", data or {})

    def __getattr__(self, name):
        if name in self._data:
            return self._data[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._data[name] = value

    def save(self):
        folder = os.path.dirname(self._file_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(self._file_path, "w") as fp:
            json.dump(self._data, fp, indent=2)


class _CombinedConfig(object):
    def __init__(self, primary, primary_saver, fallback, fallback_saver):
        object.__setattr__(self, "_primary", primary)
        object.__setattr__(self, "_primary_saver", primary_saver)
        object.__setattr__(self, "_fallback", fallback)
        object.__setattr__(self, "_fallback_saver", fallback_saver)

    def __getattr__(self, name):
        for cfg in (self._primary, self._fallback):
            if cfg is None:
                continue
            try:
                return getattr(cfg, name)
            except AttributeError:
                continue
            except Exception:
                continue
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        for cfg in (self._primary, self._fallback):
            if cfg is None:
                continue
            try:
                setattr(cfg, name, value)
            except Exception:
                continue

    def save(self):
        saved = False
        errors = []
        for saver in (self._primary_saver, self._fallback_saver):
            if not callable(saver):
                continue
            try:
                saver()
                saved = True
            except Exception as exc:
                errors.append(exc)
        if not saved and errors:
            raise errors[0]


def _legacy_local_config_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "pyRevit", "WWPTools", "Export2Ex.config.json")


def _log_file_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "pyRevit", "WWPTools", "Logs", LOG_FILE_NAME)


def log_message(message):
    try:
        log_path = _log_file_path()
        folder = os.path.dirname(log_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as fp:
            fp.write("[{}] {}\n".format(timestamp, message))
    except Exception:
        pass


def log_exception(context, exc):
    try:
        detail = traceback.format_exc()
    except Exception:
        detail = str(exc)
    log_message("{}: {}\n{}".format(context, str(exc), detail))


def describe_file_state(path):
    try:
        exists = os.path.exists(path)
    except Exception:
        exists = False
    try:
        is_file = os.path.isfile(path)
    except Exception:
        is_file = False
    try:
        size = os.path.getsize(path) if is_file else -1
    except Exception:
        size = -1
    return "path='{}' exists={} is_file={} size={}".format(path, exists, is_file, size)


def read_log_tail(max_chars=5000):
    log_path = _log_file_path()
    if not os.path.isfile(log_path):
        return "Log file not found:\n{}".format(log_path)
    try:
        with open(log_path, "r") as fp:
            text = fp.read()
    except Exception as exc:
        return "Failed to read log file '{}': {}".format(log_path, str(exc))
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def show_error_report(ex):
    log_path = _log_file_path()
    report = (
        "Export2Ex failed.\n\n"
        "Error\n{}\n\n"
        "Log File\n{}\n\n"
        "Recent Log\n{}"
    ).format(str(ex), log_path, read_log_tail())
    try:
        ui = load_uiutils()
        if hasattr(ui, "uiUtils_show_text_report"):
            ui.uiUtils_show_text_report(
                "Export2Ex Error Report",
                report,
                ok_text="Close",
                cancel_text=None,
                width=900,
                height=620,
            )
            return
        ui.uiUtils_alert(report, title="Export2Ex Error Report")
    except Exception:
        pass


def _load_local_config():
    cfg_path = _legacy_local_config_path()
    data = {}
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r") as fp:
                loaded = json.load(fp)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    return _LocalConfig(cfg_path, data)


def get_config_and_saver():
    """Persist to a project-aware JSON file and seed from legacy config when available."""
    legacy_sources = []
    try:
        from pyrevit import script as pyrevit_script

        legacy_sources.append(pyrevit_script.get_config())
    except Exception:
        pass
    legacy_files = [_legacy_local_config_path()]
    return get_tool_settings(
        "Export2Ex",
        doc=get_active_doc(),
        legacy_sources=legacy_sources,
        legacy_file_paths=legacy_files,
    )


def config_get(config, name, default=None):
    try:
        value = getattr(config, name)
    except Exception:
        return default
    return default if value is None else value


def _normalize_path(path):
    """Store paths with %USERPROFILE% prefix so configs are portable across user accounts."""
    if not path:
        return path
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile and path.lower().startswith(userprofile.lower()):
        return "%USERPROFILE%" + path[len(userprofile):]
    return path


def _is_cloud_path(path):
    if not path:
        return False
    lower = path.lower()
    return (lower.startswith("bim 360://")
            or lower.startswith("autodesk docs://")
            or lower.startswith("autodesk forma://"))


def _docs_folder():
    try:
        from System import Environment
        return Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments)
    except Exception:
        return os.path.expanduser("~")


def get_default_dir(doc):
    if doc.IsWorkshared:
        try:
            central = doc.GetWorksharingCentralModelPath()
            if central:
                path = DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(central)
                if not _is_cloud_path(path):
                    return os.path.dirname(path)
        except Exception:
            pass
    if doc.PathName and not _is_cloud_path(doc.PathName):
        return os.path.dirname(doc.PathName)
    return _docs_folder()


def ensure_existing_dir(path, fallback=""):
    if path and os.path.isdir(path):
        return path
    if fallback and os.path.isdir(fallback):
        return fallback
    return ""


def collect_schedules(doc):
    schedules = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if view.IsTemplate:
            continue
        try:
            if view.ViewType == DB.ViewType.Legend:
                continue
        except Exception:
            pass
        if view.IsTitleblockRevisionSchedule:
            continue
        schedules.append(view)
    schedules.sort(key=lambda v: v.Name)
    return schedules


def element_id_value(elem_id):
    if elem_id is None:
        return -1
    if hasattr(elem_id, "IntegerValue"):
        return _elem_id_int(elem_id)
    if hasattr(elem_id, "Value"):
        return elem_id.Value
    try:
        return int(elem_id)
    except Exception:
        return -1


class ScheduleItem(object):
    def __init__(self, view):
        self.view = view
        display_name = "{} [id:{}]".format(view.Name, element_id_value(view.Id))
        self.display_name = display_name.replace("_", "__")


def add_lib_path():
    lib_path = os.path.join(os.path.dirname(__file__), "lib")
    if lib_path not in sys.path:
        sys.path.append(lib_path)


def load_uiutils():
    script_dir = os.path.dirname(__file__)
    lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
    if lib_path not in sys.path:
        sys.path.append(lib_path)
    import WWP_uiUtils as ui
    if not hasattr(ui, "uiUtils_select_items_with_mode"):
        try:
            ui = importlib.reload(ui)
        except Exception:
            pass
    return ui


def _export_sets_to_file(saved_sets, ui, tool_name="Export2Ex"):
    """Save current saved sets to an external .settings file."""
    clr.AddReference("PresentationFramework")
    from Microsoft.Win32 import SaveFileDialog
    dlg = SaveFileDialog()
    dlg.Title = "Save Settings File"
    dlg.Filter = "Export Settings (*.settings)|*.settings|All Files (*.*)|*.*"
    dlg.DefaultExt = "settings"
    dlg.FileName = "{}_sets.settings".format(tool_name)
    if dlg.ShowDialog() != True:
        return
    try:
        payload = {"tool": tool_name, "version": "1.0", "sets": saved_sets}
        with open(dlg.FileName, "w") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        if ui:
            ui.uiUtils_alert("Settings saved to:\n{}".format(dlg.FileName), title="Save Settings")
    except Exception as exc:
        if ui:
            ui.uiUtils_alert("Could not save settings file:\n{}".format(exc), title="Save Settings")


def _import_sets_from_file(ui):
    """Load saved sets from an external .settings file. Returns dict or None if cancelled/failed."""
    clr.AddReference("PresentationFramework")
    from Microsoft.Win32 import OpenFileDialog
    dlg = OpenFileDialog()
    dlg.Title = "Load Settings File"
    dlg.Filter = "Export Settings (*.settings)|*.settings|All Files (*.*)|*.*"
    dlg.CheckFileExists = True
    if dlg.ShowDialog() != True:
        return None
    try:
        with open(dlg.FileName, "r") as f:
            data = json.load(f)
        sets = data.get("sets", {})
        if not isinstance(sets, dict):
            raise ValueError("Invalid settings file: 'sets' key missing or not a dict.")
        return sets
    except Exception as exc:
        if ui:
            ui.uiUtils_alert("Could not load settings file:\n{}".format(exc), title="Load Settings")
        return None


def _to_net_str_list(values):
    lst = List[String]()
    for v in values:
        lst.Add(str(v))
    return lst


def _show_export_settings_popup(current_settings, ui):
    """Settings popup for export options. Returns updated dict or None if cancelled."""
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows import (Window, WindowStartupLocation, Thickness, ResizeMode,
                                 SizeToContent, HorizontalAlignment, FontWeights)
    from System.Windows.Controls import (StackPanel, Button, CheckBox, RadioButton,
                                          TextBlock, ComboBox, Separator, Orientation)

    s = dict(current_settings)
    _ok = [False]

    win = Window()
    win.Title = "Export Settings"
    win.Width = 420
    win.SizeToContent = SizeToContent.Height
    win.ResizeMode = ResizeMode.NoResize
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = StackPanel()
    outer.Margin = Thickness(20)
    win.Content = outer

    def _section(text):
        tb = TextBlock()
        tb.Text = text
        tb.FontWeight = FontWeights.SemiBold
        tb.Margin = Thickness(0, 0, 0, 8)
        outer.Children.Add(tb)

    def _chk(label, key, default=False):
        cb = CheckBox()
        cb.Content = label
        cb.IsChecked = bool(s.get(key, default))
        cb.Margin = Thickness(0, 0, 0, 6)
        outer.Children.Add(cb)
        return cb

    _section("Export mode")
    mode_row = StackPanel()
    mode_row.Orientation = Orientation.Horizontal
    mode_row.Margin = Thickness(0, 0, 0, 14)
    excel_radio = RadioButton()
    excel_radio.Content = "Excel"
    excel_radio.GroupName = "ExportMode"
    excel_radio.Margin = Thickness(0, 0, 16, 0)
    csv_radio = RadioButton()
    csv_radio.Content = "CSV"
    csv_radio.GroupName = "ExportMode"
    if s.get("export_mode", 0) == 1:
        csv_radio.IsChecked = True
    else:
        excel_radio.IsChecked = True
    mode_row.Children.Add(excel_radio)
    mode_row.Children.Add(csv_radio)
    outer.Children.Add(mode_row)

    _section("Schedule appearance")
    chk_title    = _chk("Export title",                               "export_title",                False)
    chk_col_hdr  = _chk("Export column headers",                      "export_column_headers",       True)
    chk_grpd_col = _chk("Include grouped column headers",             "export_grouped_column_headers", False)
    chk_grp_hdr  = _chk("Export group headers, footers, and blank lines", "export_group_headers",    False)
    chk_use_cat  = _chk("Use category as sheet name",                 "use_category_sheet_name",     True)

    sep = Separator()
    sep.Margin = Thickness(0, 8, 0, 8)
    outer.Children.Add(sep)

    _section("CSV options")
    delim_lbl = TextBlock()
    delim_lbl.Text = "Delimiter"
    delim_lbl.Margin = Thickness(0, 0, 0, 4)
    outer.Children.Add(delim_lbl)
    delimiter_map = {"Comma (,)": ",", "Semicolon (;)": ";", "Tab (\\t)": "\t"}
    delim_combo = ComboBox()
    delim_combo.ItemsSource = _to_net_str_list(list(delimiter_map.keys()))
    delim_combo.Margin = Thickness(0, 0, 0, 10)
    cur_delim = s.get("csv_delim", ",")
    for lbl, val in delimiter_map.items():
        if val == cur_delim:
            delim_combo.SelectedItem = lbl
            break
    outer.Children.Add(delim_combo)
    chk_quote = _chk("Quote all fields", "csv_mode_quote", bool(s.get("csv_mode", 0) == 1))

    sep2 = Separator()
    sep2.Margin = Thickness(0, 8, 0, 12)
    outer.Children.Add(sep2)

    btn_row = StackPanel()
    btn_row.Orientation = Orientation.Horizontal
    btn_row.HorizontalAlignment = HorizontalAlignment.Right
    apply_btn = Button()
    apply_btn.Content = "Apply"
    apply_btn.MinWidth = 80
    apply_btn.Margin = Thickness(0, 0, 8, 0)
    cancel_btn = Button()
    cancel_btn.Content = "Cancel"
    cancel_btn.MinWidth = 80
    btn_row.Children.Add(apply_btn)
    btn_row.Children.Add(cancel_btn)
    outer.Children.Add(btn_row)

    def _apply(sv, e):
        _ok[0] = True
        win.Close()

    def _cancel(sv, e):
        win.Close()

    apply_btn.Click += _apply
    cancel_btn.Click += _cancel
    win.ShowDialog()

    if not _ok[0]:
        return None

    s["export_mode"]                  = 1 if csv_radio.IsChecked else 0
    s["export_title"]                 = bool(chk_title.IsChecked)
    s["export_column_headers"]        = bool(chk_col_hdr.IsChecked)
    s["export_grouped_column_headers"]= bool(chk_grpd_col.IsChecked)
    s["export_group_headers"]         = bool(chk_grp_hdr.IsChecked)
    s["use_category_sheet_name"]      = bool(chk_use_cat.IsChecked)
    s["csv_mode"]                     = 1 if chk_quote.IsChecked else 0
    s["csv_delim"]                    = delimiter_map.get(str(delim_combo.SelectedItem or ""), ",")
    return s


def _show_batch_dialog(saved_sets, doc, ui=None, saved_sets_param=None,
                       default_settings=None, edit_callback=None, add_callback=None):
    """Primary export UI: checkbox | set name | schedule (search) | sheet name | file path | ... | Export | Delete"""
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows import (Window, WindowStartupLocation, Thickness,
                                 HorizontalAlignment, VerticalAlignment,
                                 FontWeights, TextTrimming, GridLength, GridUnitType)
    from System.Windows.Controls import (Grid, StackPanel, ScrollViewer, Button, TextBox,
                                          ComboBox, CheckBox, TextBlock, ColumnDefinition,
                                          RowDefinition, Orientation, ScrollBarVisibility)

    _all_schedule_views = collect_schedules(doc)
    all_schedule_names = sorted(v.Name for v in _all_schedule_views)
    all_schedules_by_name = {v.Name: v for v in _all_schedule_views}
    current_settings = dict(default_settings) if default_settings else {
        "export_mode": 0, "export_title": False, "export_column_headers": True,
        "export_group_headers": False, "export_grouped_column_headers": False,
        "use_category_sheet_name": True, "csv_mode": 0, "csv_delim": ",", "csv_text_qualifier": "",
    }

    paths = {}
    set_modes = {}
    name_boxes = {}
    schedule_combos = {}
    sheet_name_labels = {}
    path_labels = {}
    checkboxes = {}
    data_row_elements = []
    _ok_clicked = [False]

    window = Window()
    window.Title = "Batch Export"
    window.Width = 1000
    window.MinWidth = 700
    window.Height = min(240 + max(len(saved_sets), 1) * 42, 660)
    window.MinHeight = 260
    window.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = Grid()
    outer.Margin = Thickness(12)
    rh = RowDefinition(); rh.Height = GridLength.Auto
    rc = RowDefinition()
    rb = RowDefinition(); rb.Height = GridLength.Auto
    outer.RowDefinitions.Add(rh)
    outer.RowDefinitions.Add(rc)
    outer.RowDefinitions.Add(rb)
    window.Content = outer

    prompt = TextBlock()
    prompt.Text = "Manage export sets. Select sets and click Export Selected to run."
    prompt.Margin = Thickness(0, 0, 0, 8)
    Grid.SetRow(prompt, 0)
    outer.Children.Add(prompt)

    scroll = ScrollViewer()
    scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
    scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    Grid.SetRow(scroll, 1)
    outer.Children.Add(scroll)

    # checkbox | set name | schedule | sheet name | file path | ... | Export | Delete
    tbl = Grid()
    for w in [28, 160, 200, 140, -1, 34, 60, 60]:
        cd = ColumnDefinition()
        cd.Width = GridLength(1, GridUnitType.Star) if w == -1 else GridLength(w)
        tbl.ColumnDefinitions.Add(cd)
    scroll.Content = tbl

    hrd = RowDefinition(); hrd.Height = GridLength.Auto
    tbl.RowDefinitions.Add(hrd)
    for col, text in [(1, "Set Name"), (2, "Schedule"), (3, "Sheet Name"), (4, "File Path")]:
        tb = TextBlock()
        tb.Text = text
        tb.FontWeight = FontWeights.Bold
        tb.Margin = Thickness(4, 2, 4, 6)
        Grid.SetRow(tb, 0); Grid.SetColumn(tb, col)
        tbl.Children.Add(tb)

    def _make_browse(sname_, mode_):
        def _on_browse(_s, _e):
            cur = paths.get(sname_) or ""
            if mode_ == 0:
                init_dir = os.path.dirname(cur) if cur else get_default_dir(doc)
                fname = os.path.basename(cur) if cur else "{}.xlsx".format(sanitize_file_name(sname_))
                new_path = _pick_save_file(
                    title="'{}' -- Choose Output File".format(sname_),
                    filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                    default_extension="xlsx", initial_directory=init_dir, file_name=fname,
                )
                new_path = normalize_excel_output_path(new_path or "")
            else:
                init_dir = cur if cur and os.path.isdir(cur) else get_default_dir(doc)
                new_path = _pick_folder("'{}' -- Choose CSV Folder".format(sname_), initial_directory=init_dir)
                new_path = (new_path or "").strip()
            if new_path:
                paths[sname_] = new_path
                path_labels[sname_].Text = new_path
        return _on_browse

    def _make_export_row(sname_):
        def _on_export(_s, _e):
            sdata = dict(saved_sets.get(sname_) or {})
            combo = schedule_combos.get(sname_)
            if combo:
                sel = str(combo.Text or (str(combo.SelectedItem) if combo.SelectedItem is not None else "")).strip()
                if sel:
                    sdata["schedule_names"] = [sel]
            mode = int(sdata.get("export_mode", current_settings.get("export_mode", 0)))
            path = paths.get(sname_) or ""
            if not path:
                init_dir = get_default_dir(doc)
                safe = re.sub(r'[\\/:*?"<>|]', "_", sname_)
                if mode == 0:
                    new_path = _pick_save_file("'{}' -- Output File".format(sname_),
                        "Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm", "xlsx", init_dir,
                        "{}.xlsx".format(safe))
                    new_path = normalize_excel_output_path(new_path or "")
                else:
                    new_path = _pick_folder("'{}' -- CSV Folder".format(sname_), init_dir)
                    new_path = (new_path or "").strip()
                if not new_path:
                    return
                paths[sname_] = new_path
                if sname_ in path_labels:
                    path_labels[sname_].Text = new_path
                path = new_path
            all_sched = {v.Name: v for v in collect_schedules(doc)}
            sched_names = sdata.get("schedule_names") or []
            views = [all_sched[n] for n in sched_names if n in all_sched]
            if not views:
                if ui:
                    ui.uiUtils_alert("No schedule '{}' found in this model.".format(", ".join(sched_names) or "(none)"), title="Export")
                return
            cs = current_settings
            ok_val = None
            if mode == 0:
                ok_val = export_to_excel(doc, views, path, ui,
                    export_title=bool(sdata.get("export_title", cs.get("export_title", False))),
                    export_column_headers=bool(sdata.get("export_column_headers", cs.get("export_column_headers", True))),
                    export_group_headers=bool(sdata.get("export_group_headers", cs.get("export_group_headers", False))),
                    export_grouped_column_headers=bool(sdata.get("export_grouped_column_headers", cs.get("export_grouped_column_headers", False))),
                    text_qualifier=sdata.get("csv_text_qualifier", cs.get("csv_text_qualifier", "")),
                    delimiter=sdata.get("csv_delim", cs.get("csv_delim", ",")),
                    use_category_sheet_name=bool(cs.get("use_category_sheet_name", True)))
                msg = "Exported '{}' successfully.".format(sname_) if ok_val else "Export failed for '{}'. Check the log.".format(sname_)
            else:
                try:
                    export_to_csv(doc, views, path,
                        quote_all=bool(int(sdata.get("csv_mode", cs.get("csv_mode", 0))) == 1),
                        delimiter=sdata.get("csv_delim", cs.get("csv_delim", ",")),
                        export_title=bool(sdata.get("export_title", cs.get("export_title", False))),
                        export_column_headers=bool(sdata.get("export_column_headers", cs.get("export_column_headers", True))),
                        export_group_headers=bool(sdata.get("export_group_headers", cs.get("export_group_headers", False))),
                        export_grouped_column_headers=bool(sdata.get("export_grouped_column_headers", cs.get("export_grouped_column_headers", False))),
                        text_qualifier=sdata.get("csv_text_qualifier", cs.get("csv_text_qualifier", "")))
                    msg = "Exported '{}' to CSV.".format(sname_)
                except Exception as exc:
                    log_exception("inline export '{}'".format(sname_), exc)
                    msg = "Export failed for '{}'. Check the log.".format(sname_)
            if ui:
                ui.uiUtils_alert(msg, title="Batch Export")
        return _on_export

    def _make_delete_row(sname_):
        def _on_delete(_s, _e):
            if sname_ in saved_sets:
                del saved_sets[sname_]
            if sname_ in paths:
                del paths[sname_]
            write_saved_sets(doc, saved_sets, saved_sets_param)
            _rebuild_rows()
        return _on_delete

    def _rebuild_rows():
        for elems in data_row_elements:
            for elem in elems:
                tbl.Children.Remove(elem)
        data_row_elements[:] = []
        while tbl.RowDefinitions.Count > 1:
            tbl.RowDefinitions.RemoveAt(1)
        checkboxes.clear(); path_labels.clear(); name_boxes.clear()
        schedule_combos.clear(); sheet_name_labels.clear()

        current_names = sorted(saved_sets.keys())
        if not current_names:
            rd = RowDefinition(); rd.Height = GridLength.Auto
            tbl.RowDefinitions.Add(rd)
            empty_tb = TextBlock()
            empty_tb.Text = "No saved sets. Click 'Add Set' to create one."
            empty_tb.Margin = Thickness(4, 16, 4, 4)
            empty_tb.HorizontalAlignment = HorizontalAlignment.Center
            Grid.SetRow(empty_tb, 1); Grid.SetColumnSpan(empty_tb, 8)
            tbl.Children.Add(empty_tb)
            data_row_elements.append([empty_tb])
            return

        for row_idx, sname in enumerate(current_names, 1):
            sdata = saved_sets.get(sname) or {}
            mode = int(sdata.get("export_mode", current_settings.get("export_mode", 0)))
            set_modes[sname] = mode
            if sname not in paths:
                raw = sdata.get("excel_path") if mode == 0 else sdata.get("csv_folder")
                paths[sname] = (raw or "").strip()

            rd = RowDefinition(); rd.Height = GridLength(42)
            tbl.RowDefinitions.Add(rd)
            row_elems = []

            cb = CheckBox(); cb.IsChecked = True
            cb.VerticalAlignment = VerticalAlignment.Center
            cb.HorizontalAlignment = HorizontalAlignment.Center
            Grid.SetRow(cb, row_idx); Grid.SetColumn(cb, 0)
            tbl.Children.Add(cb); checkboxes[sname] = cb; row_elems.append(cb)

            name_box = TextBox(); name_box.Text = sname
            name_box.VerticalAlignment = VerticalAlignment.Center
            name_box.Margin = Thickness(2, 3, 4, 3)
            Grid.SetRow(name_box, row_idx); Grid.SetColumn(name_box, 1)
            tbl.Children.Add(name_box); name_boxes[sname] = name_box; row_elems.append(name_box)

            sched_combo = ComboBox(); sched_combo.IsEditable = True
            sched_combo.IsTextSearchEnabled = False  # handled manually below
            for n in all_schedule_names:
                sched_combo.Items.Add(n)
            existing = sdata.get("schedule_names") or []
            if existing:
                sched_combo.Text = existing[0]
            sched_combo.VerticalAlignment = VerticalAlignment.Center
            sched_combo.Margin = Thickness(2, 3, 4, 3)
            Grid.SetRow(sched_combo, row_idx); Grid.SetColumn(sched_combo, 2)
            tbl.Children.Add(sched_combo); schedule_combos[sname] = sched_combo; row_elems.append(sched_combo)

            _init_sched = existing[0] if existing else ""
            if _init_sched and current_settings.get("use_category_sheet_name", True):
                _iv = all_schedules_by_name.get(_init_sched)
                _init_sheet = sanitize_sheet_name(_pluralize(_get_schedule_category_name(doc, _iv))) if _iv else sanitize_sheet_name(_init_sched)
            else:
                _init_sheet = sanitize_sheet_name(_init_sched) if _init_sched else ""
            sheet_lbl = TextBlock()
            sheet_lbl.Text = _init_sheet
            sheet_lbl.VerticalAlignment = VerticalAlignment.Center
            sheet_lbl.Margin = Thickness(4, 0, 4, 0)
            sheet_lbl.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetRow(sheet_lbl, row_idx); Grid.SetColumn(sheet_lbl, 3)
            tbl.Children.Add(sheet_lbl); sheet_name_labels[sname] = sheet_lbl; row_elems.append(sheet_lbl)

            def _wire_combo(combo_, lbl_, names_):
                _busy = [False]
                def _compute_sheet(sel):
                    if not sel:
                        return ""
                    if current_settings.get("use_category_sheet_name", True):
                        view = all_schedules_by_name.get(sel)
                        if view:
                            return sanitize_sheet_name(_pluralize(_get_schedule_category_name(doc, view)))
                    return sanitize_sheet_name(sel)
                def _update_lbl():
                    lbl_.Text = _compute_sheet(str(combo_.Text or "").strip())
                def _on_key_up(s, e):
                    from System.Windows.Input import Key
                    if _busy[0]:
                        return
                    if e.Key in (Key.Up, Key.Down, Key.Enter, Key.Return, Key.Tab, Key.Escape):
                        _update_lbl()
                        return
                    text = str(combo_.Text or "").strip().lower()
                    filtered = [n for n in names_ if text in n.lower()] if text else names_
                    saved_text = combo_.Text
                    _busy[0] = True
                    combo_.Items.Clear()
                    for n in filtered:
                        combo_.Items.Add(n)
                    combo_.Text = saved_text
                    _busy[0] = False
                    if filtered and not combo_.IsDropDownOpen:
                        combo_.IsDropDownOpen = True
                    _update_lbl()
                def _on_sel_changed(s, e):
                    _update_lbl()
                combo_.PreviewKeyUp += _on_key_up
                combo_.SelectionChanged += _on_sel_changed
                _update_lbl()
            _wire_combo(sched_combo, sheet_lbl, all_schedule_names)

            path_tb = TextBlock()
            path_tb.Text = paths.get(sname) or "(no path)"
            path_tb.VerticalAlignment = VerticalAlignment.Center
            path_tb.Margin = Thickness(4, 0, 4, 0)
            path_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetRow(path_tb, row_idx); Grid.SetColumn(path_tb, 4)
            tbl.Children.Add(path_tb); path_labels[sname] = path_tb; row_elems.append(path_tb)

            browse_btn = Button(); browse_btn.Content = "..."
            browse_btn.Margin = Thickness(2, 3, 2, 3)
            Grid.SetRow(browse_btn, row_idx); Grid.SetColumn(browse_btn, 5)
            tbl.Children.Add(browse_btn)
            browse_btn.Click += _make_browse(sname, mode); row_elems.append(browse_btn)

            export_btn = Button(); export_btn.Content = "Export"
            export_btn.Margin = Thickness(2, 3, 2, 3)
            Grid.SetRow(export_btn, row_idx); Grid.SetColumn(export_btn, 6)
            tbl.Children.Add(export_btn)
            export_btn.Click += _make_export_row(sname); row_elems.append(export_btn)

            del_btn = Button(); del_btn.Content = "Delete"
            del_btn.Margin = Thickness(2, 3, 2, 3)
            Grid.SetRow(del_btn, row_idx); Grid.SetColumn(del_btn, 7)
            tbl.Children.Add(del_btn)
            del_btn.Click += _make_delete_row(sname); row_elems.append(del_btn)

            data_row_elements.append(row_elems)

    _rebuild_rows()

    # Footer: Settings (left) | Add Set + Save + Export Selected + Cancel (right)
    footer = Grid()
    footer.Margin = Thickness(0, 10, 0, 0)
    Grid.SetRow(footer, 2)
    outer.Children.Add(footer)

    left_btns = StackPanel()
    left_btns.Orientation = Orientation.Horizontal
    left_btns.HorizontalAlignment = HorizontalAlignment.Left
    footer.Children.Add(left_btns)

    settings_btn = Button(); settings_btn.Content = "Settings"
    settings_btn.MinWidth = 80; settings_btn.Margin = Thickness(0, 0, 6, 0)

    save_file_btn = Button(); save_file_btn.Content = "Save Settings..."
    save_file_btn.MinWidth = 100; save_file_btn.Margin = Thickness(0, 0, 6, 0)

    load_file_btn = Button(); load_file_btn.Content = "Load Settings..."
    load_file_btn.MinWidth = 100

    def _open_settings(_s, _e):
        new_s = _show_export_settings_popup(current_settings, ui)
        if new_s is not None:
            current_settings.clear()
            current_settings.update(new_s)
            use_cat = current_settings.get("use_category_sheet_name", True)
            for sname, combo in list(schedule_combos.items()):
                lbl = sheet_name_labels.get(sname)
                if not lbl:
                    continue
                sel = str(combo.Text or "").strip()
                if not sel:
                    lbl.Text = ""
                    continue
                if use_cat:
                    view = all_schedules_by_name.get(sel)
                    lbl.Text = sanitize_sheet_name(_pluralize(_get_schedule_category_name(doc, view))) if view else sanitize_sheet_name(sel)
                else:
                    lbl.Text = sanitize_sheet_name(sel)

    def _save_file(_s, _e):
        _export_sets_to_file(saved_sets, ui, tool_name="Export2Ex")

    def _load_file(_s, _e):
        imported = _import_sets_from_file(ui)
        if imported is not None:
            saved_sets.update(imported)
            write_saved_sets(doc, saved_sets, saved_sets_param)
            _rebuild_rows()

    settings_btn.Click += _open_settings
    save_file_btn.Click += _save_file
    load_file_btn.Click += _load_file
    left_btns.Children.Add(settings_btn)
    left_btns.Children.Add(save_file_btn)
    left_btns.Children.Add(load_file_btn)

    right_btns = StackPanel()
    right_btns.Orientation = Orientation.Horizontal
    right_btns.HorizontalAlignment = HorizontalAlignment.Right
    footer.Children.Add(right_btns)

    add_btn = Button(); add_btn.Content = "Add Set"
    add_btn.MinWidth = 80; add_btn.Margin = Thickness(0, 0, 6, 0)

    save_btn = Button(); save_btn.Content = "Save"
    save_btn.MinWidth = 80; save_btn.Margin = Thickness(0, 0, 20, 0)

    ok_btn = Button(); ok_btn.Content = "Export Selected"
    ok_btn.MinWidth = 110; ok_btn.Margin = Thickness(0, 0, 6, 0)

    cancel_btn = Button(); cancel_btn.Content = "Cancel"
    cancel_btn.MinWidth = 70

    def _add(_s, _e):
        idx = 1
        while "New Set {}".format(idx) in saved_sets:
            idx += 1
        new_name = "New Set {}".format(idx)
        saved_sets[new_name] = dict(current_settings)
        saved_sets[new_name]["schedule_names"] = []
        _rebuild_rows()

    def _save(_s, _e):
        renames = [(orig, (nb.Text or "").strip())
                   for orig, nb in list(name_boxes.items())
                   if (nb.Text or "").strip() and (nb.Text or "").strip() != orig]
        for orig, new in renames:
            if orig in saved_sets:
                saved_sets[new] = saved_sets.pop(orig)
                if orig in paths: paths[new] = paths.pop(orig)
                if orig in schedule_combos: schedule_combos[new] = schedule_combos.pop(orig)
        for sname, combo in list(schedule_combos.items()):
            sel = str(combo.Text or (str(combo.SelectedItem) if combo.SelectedItem is not None else "")).strip()
            if sel and sname in saved_sets:
                saved_sets[sname]["schedule_names"] = [sel]
        for sname, path_val in list(paths.items()):
            if path_val and sname in saved_sets:
                mode = int(saved_sets[sname].get("export_mode", 0))
                saved_sets[sname]["excel_path" if mode == 0 else "csv_folder"] = path_val
        proj_data_back = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param))
        proj_data_back["sets"] = saved_sets
        ok_saved = write_saved_sets(doc, proj_data_back, saved_sets_param)
        if ui:
            ui.uiUtils_alert("Sets saved." if ok_saved else "Could not save to Project Information.", title="Batch Export")
        _rebuild_rows()

    def _ok(_s, _e):
        _ok_clicked[0] = True
        window.Close()

    def _cancel(_s, _e):
        window.Close()

    add_btn.Click += _add; save_btn.Click += _save
    ok_btn.Click += _ok; cancel_btn.Click += _cancel
    right_btns.Children.Add(add_btn); right_btns.Children.Add(save_btn)
    right_btns.Children.Add(ok_btn); right_btns.Children.Add(cancel_btn)

    window.ShowDialog()

    if not _ok_clicked[0]:
        return None

    current_names = sorted(saved_sets.keys())
    selected = [sname for sname in current_names if checkboxes.get(sname) and checkboxes[sname].IsChecked]
    return selected, dict(paths), dict(current_settings)


def _show_export_form(
    ui,
    items,
    schedule_names,
    prechecked_indices,
    init_excel_path,
    init_csv_dir,
    last_csv_delim,
    last_csv_mode,
    last_export_mode,
    last_export_title,
    last_column_headers,
    last_group_headers,
    last_grouped_column_headers,
    last_text_qualifier,
    saved_sets,
    doc,
    last_use_category_sheet_name=False,
    saved_sets_param_name=None,
):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.IO import StringReader
    from System.Windows.Markup import XamlReader
    from System.Xml import XmlReader
    from System.Windows.Controls import SelectionMode

    def _to_net_list(values):
        net_list = List[String]()
        for value in values:
            net_list.Add("" if value is None else str(value))
        return net_list

    dialog_script_dir = os.path.dirname(__file__)
    xaml_path = os.path.join(dialog_script_dir, "ExportSchedulesDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))
    xaml_text = File.ReadAllText(xaml_path)
    reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(reader)
    apply_window_title(window, "Multiple Schedules Exporter")

    search_box = window.FindName("SearchBox")
    schedule_list = window.FindName("ScheduleList")
    excel_mode = window.FindName("ExcelMode")
    csv_mode = window.FindName("CsvMode")
    csv_folder = window.FindName("CsvFolder")
    browse_csv = window.FindName("BrowseCsv")
    csv_delim = window.FindName("CsvDelimiter")
    text_qualifier = window.FindName("TextQualifier")
    quote_all = window.FindName("QuoteAll")
    export_title = window.FindName("ExportTitle")
    export_column_headers = window.FindName("ExportColumnHeaders")
    export_group_headers = window.FindName("ExportGroupHeaders")
    export_grouped_column_headers = window.FindName("ExportGroupedColumnHeaders")
    cancel_button = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")
    use_category_sheet_name_ctrl = window.FindName("UseCategorySheetName")
    batch_export_button = window.FindName("BatchExportButton")
    csv_output_options = window.FindName("CsvOutputOptions")

    schedule_list.ItemsSource = _to_net_list(items)
    delimiter_items = [
        "Comma (,)",
        "Semicolon (;)",
        "Tab (\\t)",
    ]
    csv_delim.ItemsSource = _to_net_list(delimiter_items)

    qualifier_items = [
        "(none)",
        'Double Quote (")',
        "Single Quote (')",
    ]
    text_qualifier.ItemsSource = _to_net_list(qualifier_items)

    delimiter_values = {
        "Comma (,)": ",",
        "Semicolon (;)": ";",
        "Tab (\\t)": "\t",
    }
    default_delim_label = "Comma (,)"
    for label, value in delimiter_values.items():
        if value == last_csv_delim:
            default_delim_label = label
            break

    csv_folder.Text = init_csv_dir or ""
    quote_all.IsChecked = bool(last_csv_mode == 1)
    csv_delim.SelectedItem = default_delim_label

    export_title.IsChecked = bool(last_export_title)
    export_column_headers.IsChecked = bool(last_column_headers)
    export_group_headers.IsChecked = bool(last_group_headers)
    export_grouped_column_headers.IsChecked = bool(last_grouped_column_headers)
    qualifier_label = "(none)"
    if last_text_qualifier == "\"":
        qualifier_label = 'Double Quote (")'
    elif last_text_qualifier == "'":
        qualifier_label = "Single Quote (')"
    text_qualifier.SelectedItem = qualifier_label

    if last_export_mode == 1:
        csv_mode.IsChecked = True
    else:
        excel_mode.IsChecked = True

    if use_category_sheet_name_ctrl is not None:
        use_category_sheet_name_ctrl.IsChecked = bool(last_use_category_sheet_name)

    lib_path = os.path.abspath(os.path.join(dialog_script_dir, "..", "..", "..", "lib"))
    ui.uiUtils_load_logo(logo_image, os.path.join(lib_path, "WWPtools-logo.png"))

    selected_names = set()
    if prechecked_indices:
        for idx in prechecked_indices:
            if 0 <= idx < len(items):
                selected_names.add(items[idx])

    def _apply_selection():
        schedule_list.SelectedItems.Clear()
        for item in schedule_list.Items:
            if str(item) in selected_names:
                schedule_list.SelectedItems.Add(item)

    _apply_selection()

    def _update_enabled_state():
        from System.Windows import Visibility
        is_excel = bool(excel_mode.IsChecked)
        excel_path.IsEnabled = is_excel
        browse_excel.IsEnabled = is_excel
        csv_folder.IsEnabled = not is_excel
        browse_csv.IsEnabled = not is_excel
        if use_category_sheet_name_ctrl is not None:
            use_category_sheet_name_ctrl.IsEnabled = is_excel
        if csv_output_options is not None:
            csv_output_options.Visibility = Visibility.Collapsed if is_excel else Visibility.Visible

    def _filter_list(_sender=None, _args=None):
        text = search_box.Text or ""
        text = text.strip().lower()
        if not text:
            filtered = items
        else:
            filtered = [item for item in items if text in item.lower()]
        schedule_list.ItemsSource = _to_net_list(filtered)
        _apply_selection()

    def _selection_changed(_sender, _args):
        # Keep export selection aligned with what is currently highlighted in the UI.
        selected_names.clear()
        for item in schedule_list.SelectedItems:
            selected_names.add(str(item))

    def _refresh_saved_set_dropdown():
        if saved_set_box is None:
            return
        current_text = saved_set_box.Text or ""
        saved_set_box.Items.Clear()
        for name in sorted(saved_sets.keys()):
            saved_set_box.Items.Add(name)
        saved_set_box.Text = current_text
        if batch_export_button is not None:
            batch_export_button.IsEnabled = bool(saved_sets)

    def _get_current_set_data():
        selected_indices = [idx for idx, item in enumerate(items) if item in selected_names]
        return {
            "schedule_names": [schedule_names[idx] for idx in selected_indices if idx < len(schedule_names)],
            "export_mode": 0 if excel_mode.IsChecked else 1,
            "csv_mode": 1 if bool(quote_all.IsChecked) else 0,
            "csv_delim": delimiter_values.get(str(csv_delim.SelectedItem), ","),
            "csv_text_qualifier": "\"" if str(text_qualifier.SelectedItem).startswith("Double") else ("'" if str(text_qualifier.SelectedItem).startswith("Single") else ""),
            "export_title": bool(export_title.IsChecked),
            "export_column_headers": bool(export_column_headers.IsChecked),
            "export_group_headers": bool(export_group_headers.IsChecked),
            "export_grouped_column_headers": bool(export_grouped_column_headers.IsChecked),
            "csv_folder": csv_folder.Text or "",
            "use_category_sheet_name": bool(use_category_sheet_name_ctrl.IsChecked) if use_category_sheet_name_ctrl is not None else False,
        }

    def _save_sets_to_project():
        proj_data = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param_name))
        proj_data["settings"] = proj_data.get("settings", {})
        proj_data["sets"] = saved_sets
        if not write_saved_sets(doc, proj_data, saved_sets_param_name):
            ui.uiUtils_alert(
                "Could not save sets to '{}' on Project Information.\n"
                "Check the parameter is not read-only.".format(
                    saved_sets_param_name or PARAM_SAVED_SETS
                ),
                title="Export Schedules",
            )

    def _apply_saved_set(set_data):
        try:
            selected_set = set(set_data.get("schedule_names") or [])
            selected_names.clear()
            for idx, schedule_name in enumerate(schedule_names):
                if schedule_name in selected_set and idx < len(items):
                    selected_names.add(items[idx])
            _apply_selection()

            mode_value = int(set_data.get("export_mode", 0))
            if mode_value == 1:
                csv_mode.IsChecked = True
            else:
                excel_mode.IsChecked = True
            _update_enabled_state()

            quote_all.IsChecked = bool(int(set_data.get("csv_mode", 0)) == 1)
            saved_delim = set_data.get("csv_delim", ",")
            delim_label = default_delim_label
            for label, value in delimiter_values.items():
                if value == saved_delim:
                    delim_label = label
                    break
            csv_delim.SelectedItem = delim_label

            saved_qualifier = set_data.get("csv_text_qualifier", "")
            if saved_qualifier == "\"":
                text_qualifier.SelectedItem = 'Double Quote (")'
            elif saved_qualifier == "'":
                text_qualifier.SelectedItem = "Single Quote (')"
            else:
                text_qualifier.SelectedItem = "(none)"

            export_title.IsChecked = bool(set_data.get("export_title", False))
            export_column_headers.IsChecked = bool(set_data.get("export_column_headers", True))
            export_group_headers.IsChecked = bool(set_data.get("export_group_headers", False))
            export_grouped_column_headers.IsChecked = bool(set_data.get("export_grouped_column_headers", False))
            if use_category_sheet_name_ctrl is not None:
                use_category_sheet_name_ctrl.IsChecked = bool(set_data.get("use_category_sheet_name", False))
            excel_path.Text = set_data.get("excel_path") or ""
            csv_folder.Text = set_data.get("csv_folder") or ""
        except Exception:
            pass

    def _load_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name:
            return
        set_data = saved_sets.get(name)
        if not isinstance(set_data, dict):
            return
        _apply_saved_set(set_data)

    def _save_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name:
            return
        saved_sets[name] = _get_current_set_data()
        _save_sets_to_project()
        _refresh_saved_set_dropdown()

    def _delete_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name or name not in saved_sets:
            return
        del saved_sets[name]
        _save_sets_to_project()
        saved_set_box.Text = ""
        _refresh_saved_set_dropdown()

    _batch_result = [None]

    def _batch_export(_sender=None, _args=None):
        if not saved_sets:
            ui.uiUtils_alert("No saved sets available. Save a set first.", title="Multiple Schedules Exporter")
            return

        def _edit_set(sname, sdata):
            _apply_saved_set(sdata)
            saved_set_box.Text = sname

        def _add_set():
            saved_set_box.Text = ""
            _apply_saved_set({})

        result = _show_batch_dialog(
            saved_sets, doc,
            ui=ui,
            saved_sets_param=saved_sets_param_name,
            edit_callback=_edit_set,
            add_callback=_add_set,
        )
        if not result:
            return
        chosen, override_paths = result
        if not chosen:
            return
        _batch_result[0] = {"names": chosen, "paths": override_paths}
        window.DialogResult = True
        window.Close()

    def _browse_excel(_sender, _args):
        current = excel_path.Text or ""
        file_name = os.path.basename(current) if current else "Schedules.xlsx"
        init_dir = ensure_existing_dir(
            os.path.dirname(current) if current else "",
            os.path.dirname(init_excel_path) if init_excel_path else "",
        )
        try:
            file_path = _pick_save_file(
                title="Export Schedules",
                filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                default_extension="xlsx",
                initial_directory=init_dir,
                file_name=file_name,
            )
        except Exception as exc:
            log_exception("Native browse Excel dialog failed", exc)
            try:
                file_path = _pick_save_file(
                    title="Export Schedules",
                    filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                    default_extension="xlsx",
                    initial_directory="",
                    file_name=file_name,
                )
            except Exception as retry_exc:
                log_exception("Native browse Excel dialog retry failed", retry_exc)
                ui.uiUtils_alert(
                    "Could not open the Excel save dialog. Check the suggested path and try again.",
                    title="Multiple Schedules Exporter",
                )
                return
        if file_path:
            excel_path.Text = file_path

    def _browse_csv(_sender, _args):
        init_dir = ensure_existing_dir(csv_folder.Text or "", init_csv_dir)
        folder = _pick_folder(
            title="Select CSV Folder",
            initial_directory=init_dir,
        )
        if folder:
            csv_folder.Text = folder

    def _ok(_sender, _args):
        window.DialogResult = True
        window.Close()

    def _cancel(_sender, _args):
        window.DialogResult = False
        window.Close()

    excel_mode.Checked += lambda s, e: _update_enabled_state()
    csv_mode.Checked += lambda s, e: _update_enabled_state()
    search_box.TextChanged += _filter_list
    schedule_list.SelectionChanged += _selection_changed
    browse_excel.Click += _browse_excel
    browse_csv.Click += _browse_csv
    ok_button.Click += _ok
    cancel_button.Click += _cancel
    if load_set_button is not None:
        load_set_button.Click += _load_set
    if save_set_button is not None:
        save_set_button.Click += _save_set
    if delete_set_button is not None:
        delete_set_button.Click += _delete_set
    if batch_export_button is not None:
        batch_export_button.Click += _batch_export

    _refresh_saved_set_dropdown()
    _update_enabled_state()

    if not window.ShowDialog():
        return None

    if _batch_result[0] is not None:
        bd = _batch_result[0]
        return {"batch_sets": bd["names"], "batch_paths": bd["paths"]}

    selected_indices = [idx for idx, item in enumerate(items) if item in selected_names]
    selected_mode = 0 if excel_mode.IsChecked else 1
    delimiter_label = str(csv_delim.SelectedItem) if csv_delim.SelectedItem else default_delim_label
    delimiter = delimiter_values.get(delimiter_label, ",")
    qualifier_label = str(text_qualifier.SelectedItem) if text_qualifier.SelectedItem else "(none)"
    qualifier_value = ""
    if qualifier_label.startswith("Double"):
        qualifier_value = "\""
    elif qualifier_label.startswith("Single"):
        qualifier_value = "'"

    return {
        "selected_indices": selected_indices,
        "mode": selected_mode,
        "excel_path": excel_path.Text or "",
        "csv_folder": csv_folder.Text or "",
        "csv_delimiter": delimiter,
        "csv_quote_all": bool(quote_all.IsChecked),
        "csv_text_qualifier": qualifier_value,
        "export_title": bool(export_title.IsChecked),
        "export_column_headers": bool(export_column_headers.IsChecked),
        "export_group_headers": bool(export_group_headers.IsChecked),
        "export_grouped_column_headers": bool(export_grouped_column_headers.IsChecked),
        "use_category_sheet_name": bool(use_category_sheet_name_ctrl.IsChecked) if use_category_sheet_name_ctrl is not None else False,
    }


def read_csv_rows(path, delimiter=",", quotechar=""):
    log_message(
        "read_csv_rows start {} delimiter={!r} quotechar={!r}".format(
            describe_file_state(path), delimiter, quotechar
        )
    )
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        for attempt in range(1, 4):
            try:
                text = read_text_file(path, encoding=encoding)
                handle = io.StringIO(text)
                if quotechar in ("\"", "'"):
                    reader = csv.reader(handle, delimiter=delimiter, quotechar=quotechar)
                else:
                    reader = csv.reader(handle, delimiter=delimiter)
                rows = [row for row in reader]
                log_message(
                    "read_csv_rows success path='{}' encoding={} attempt={} rows={}".format(
                        path, encoding, attempt, len(rows)
                    )
                )
                return rows
            except Exception as exc:
                log_message(
                    "read_csv_rows failed path='{}' encoding={} attempt={} {} error={}".format(
                        path, encoding, attempt, describe_file_state(path), str(exc)
                    )
                )
                if attempt < 3:
                    time.sleep(0.2)
    return []


def get_text_encoding(name):
    value = (name or "").strip().lower()
    if value == "utf-8-sig":
        return UTF8Encoding(True)
    if value == "utf-8":
        return UTF8Encoding(False)
    if value == "utf-16":
        return Encoding.Unicode
    return Encoding.GetEncoding(name)


def read_text_file(path, encoding="utf-8"):
    log_message("read_text_file open {} encoding={}".format(describe_file_state(path), encoding))
    reader = StreamReader(path, get_text_encoding(encoding), True)
    try:
        text = reader.ReadToEnd()
        log_message("read_text_file success path='{}' chars={}".format(path, len(text)))
        return text
    finally:
        reader.Close()


def write_text_file(path, text, encoding="utf-8"):
    log_message(
        "write_text_file start path='{}' encoding={} chars={} parent_exists={}".format(
            path,
            encoding,
            len(text or ""),
            os.path.isdir(os.path.dirname(path)) if os.path.dirname(path) else True,
        )
    )
    for attempt in range(1, 4):
        writer = None
        try:
            writer = StreamWriter(path, False, get_text_encoding(encoding))
            writer.Write(text)
            writer.Flush()
            log_message(
                "write_text_file success path='{}' attempt={} {}".format(
                    path, attempt, describe_file_state(path)
                )
            )
            return
        except Exception as exc:
            log_message(
                "write_text_file failed path='{}' attempt={} {} error={}".format(
                    path, attempt, describe_file_state(path), str(exc)
                )
            )
            if attempt >= 3:
                raise
            time.sleep(0.2)
        finally:
            if writer is not None:
                try:
                    writer.Close()
                except Exception:
                    pass


def normalize_table_data(data):
    return data


def get_section_data(view, section_type):
    table = view.GetTableData()
    try:
        return table.GetSectionData(section_type)
    except Exception:
        return None


def get_section_row_count(view, section_type):
    section = get_section_data(view, section_type)
    if section is None:
        return 0
    try:
        return section.NumberOfRows
    except Exception:
        return 0


def is_key_schedule(view):
    try:
        definition = view.Definition
    except Exception:
        definition = None
    try:
        return bool(definition and definition.IsKeySchedule)
    except Exception:
        return False


def get_body_row_element_ids(view):
    section = get_section_data(view, DB.SectionType.Body)
    if section is None:
        return []
    ids = []
    for row in range(section.NumberOfRows):
        elem_value = ""
        try:
            col_count = section.NumberOfColumns
        except Exception:
            col_count = 0
        for col in range(col_count):
            try:
                elem_id = section.GetCellElementId(row, col)
            except Exception:
                elem_id = None
            elem_int = element_id_value(elem_id)
            if elem_int != -1:
                elem_value = str(elem_int)
                break
        ids.append(elem_value)
    return ids


def inject_element_id_column(data, view, csv_text=False):
    if not data:
        return data
    header_rows = get_section_row_count(view, DB.SectionType.Header)
    body_ids = get_body_row_element_ids(view)
    total_rows = len(data)
    if header_rows > total_rows:
        header_rows = total_rows
    body_rows = min(len(body_ids), max(0, total_rows - header_rows))
    for idx, row in enumerate(data):
        if row is None:
            row = []
        if idx < header_rows:
            if idx == max(0, header_rows - 1):
                row.append("ElementId")
            else:
                row.append("")
        elif idx < header_rows + body_rows:
            elem_value = body_ids[idx - header_rows]
            if csv_text and elem_value and elem_value.isdigit() and len(elem_value) > 11:
                elem_value = "'" + elem_value
            row.append(elem_value)
        else:
            row.append("")
        data[idx] = row
    return data




def write_table_to_sheet(sheet, data, start_row, header_rows=0, column_specs=None, doc=None):
    if not data:
        return
    has_specs = bool(column_specs) and any(spec is not None for spec in column_specs)
    row_idx = start_row
    for row_offset, row in enumerate(data):
        col_idx = 1
        for value in row:
            spec = None
            if has_specs and column_specs and (col_idx - 1) < len(column_specs):
                spec = column_specs[col_idx - 1]
            if row_offset < header_rows:
                cell_value = value
            elif has_specs:
                if spec is None:
                    cell_value = value
                else:
                    cell_value = coerce_cell_value(value, spec=spec, doc=doc, numeric_fallback=True)
            else:
                cell_value = coerce_cell_value(value, spec=None, doc=None, numeric_fallback=False)
            cell = sheet.cell(row=row_idx, column=col_idx, value=cell_value)
            if row_offset >= header_rows and col_idx == len(row):
                if cell_value is None:
                    cell_value = ""
                cell.value = str(cell_value)
                cell.number_format = "@"
            col_idx += 1
        row_idx += 1


_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def coerce_cell_value(value, spec=None, doc=None, numeric_fallback=True):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return ""
        if not numeric_fallback:
            return value
        if "'" in text or '"' in text or "/" in text:
            return value
        if re.search(r"\d[A-Za-z]", text):
            return value
        if _NUMERIC_RE.match(text):
            if spec is None:
                if text.startswith("0") and len(text) > 1 and not text.startswith("0."):
                    return value
                if text.startswith("-0") and len(text) > 2 and not text.startswith("-0."):
                    return value
            try:
                return int(text)
            except Exception:
                pass
            try:
                return float(text)
            except Exception:
                return value
    return value


def get_column_specs(view):
    section = get_section_data(view, DB.SectionType.Body)
    if section is None:
        return []
    try:
        col_count = int(section.NumberOfColumns)
    except Exception:
        col_count = 0
    specs = [None] * col_count
    try:
        definition = view.Definition
        field_ids = list(definition.GetFieldOrder())
        col = 0
        for field_id in field_ids:
            try:
                field = definition.GetField(field_id)
            except Exception:
                continue
            try:
                if field.IsHidden:
                    continue
            except Exception:
                pass
            if col >= col_count:
                break
            spec = None
            try:
                spec = field.GetSpecTypeId()
            except Exception:
                pass
            if spec is None:
                try:
                    spec = field.UnitType
                except Exception:
                    pass
            specs[col] = spec
            col += 1
    except Exception:
        pass
    return specs


def make_unique_name(base, used, max_len=None):
    candidate = base
    if max_len:
        candidate = candidate[:max_len]
    if candidate not in used:
        used.add(candidate)
        return candidate
    idx = 1
    while True:
        suffix = "_{}".format(idx)
        trunc = candidate
        if max_len:
            trunc = candidate[: max_len - len(suffix)]
        name = "{}{}".format(trunc, suffix)
        if name not in used:
            used.add(name)
            return name
        idx += 1


def _try_set_option(options, names, value):
    for name in names:
        if not hasattr(options, name):
            continue
        try:
            setattr(options, name, value)
            return True
        except Exception:
            continue
    return False


def apply_schedule_export_options(
    options,
    delimiter=",",
    export_title=False,
    export_column_headers=True,
    export_group_headers=False,
    export_grouped_column_headers=False,
    text_qualifier="",
):
    options.FieldDelimiter = delimiter
    # Revit API property names differ by version; support both.
    _try_set_option(options, ("ExportTitle", "Title"), bool(export_title))
    _try_set_option(options, ("ExportColumnHeaders", "ColumnHeaders"), bool(export_column_headers))
    _try_set_option(options, ("ExportGroupHeaders", "HeadersFootersBlanks"), bool(export_group_headers))
    _try_set_option(options, ("ExportGroupedColumnHeaders",), bool(export_grouped_column_headers))

    if text_qualifier in ("\"", "'"):
        _try_set_option(options, ("TextQualifier",), text_qualifier)
    else:
        _try_set_option(options, ("TextQualifier",), "")


def export_to_excel(
    doc,
    schedules,
    file_path,
    ui,
    export_title=False,
    export_column_headers=True,
    export_group_headers=False,
    export_grouped_column_headers=False,
    text_qualifier="",
    delimiter=",",
    use_category_sheet_name=False,
):
    log_message(
        "export_to_excel start file_path='{}' schedules={} delimiter={!r} quotechar={!r} title={} col_headers={} group_headers={} grouped_col_headers={}".format(
            file_path,
            len(schedules),
            delimiter,
            text_qualifier,
            bool(export_title),
            bool(export_column_headers),
            bool(export_group_headers),
            bool(export_grouped_column_headers),
        )
    )
    add_lib_path()
    try:
        import WWP_xlsx as openpyxl
    except Exception as exc:
        ui.uiUtils_alert(
            "Excel writer is not available.\n{}".format(exc),
            title="Multiple Schedules Exporter",
        )
        return False

    used_names = set()
    if os.path.exists(file_path):
        load_kwargs = {}
        if os.path.splitext(file_path)[1].lower() == ".xlsm":
            load_kwargs["keep_vba"] = True
        workbook = openpyxl.load_workbook(file_path, **load_kwargs)
    else:
        workbook = openpyxl.Workbook()
    temp_dir = tempfile.mkdtemp(prefix="wwp_schedules_")

    options = DB.ViewScheduleExportOptions()
    apply_schedule_export_options(
        options,
        delimiter=delimiter,
        export_title=export_title,
        export_column_headers=export_column_headers,
        export_group_headers=export_group_headers,
        export_grouped_column_headers=export_grouped_column_headers,
        text_qualifier=text_qualifier,
    )
    try:
        for view in schedules:
            log_message("export_to_excel schedule start name='{}' id={}".format(view.Name, element_id_value(view.Id)))
            key_schedule = is_key_schedule(view)
            raw_name = _pluralize(_get_schedule_category_name(doc, view)) if use_category_sheet_name else view.Name
            base_name = sanitize_sheet_name(raw_name)
            if base_name in workbook.sheetnames and base_name not in used_names:
                sheet_name = base_name
            else:
                used_pool = set(workbook.sheetnames)
                used_pool.update(used_names)
                sheet_name = make_unique_name(base_name, used_pool, max_len=31)
            used_names.add(sheet_name)
            if sheet_name in workbook.sheetnames:
                existing = workbook[sheet_name]
                sheet_idx = workbook.worksheets.index(existing)
                workbook.remove(existing)
                sheet = workbook.create_sheet(title=sheet_name, index=sheet_idx)
            else:
                sheet = workbook.create_sheet(title=sheet_name)

            temp_name = "{}.csv".format(sanitize_file_name(view.Name))
            log_message("export_to_excel view.Export temp_dir='{}' temp_name='{}'".format(temp_dir, temp_name))
            view.Export(temp_dir, temp_name, options)
            csv_path = os.path.join(temp_dir, temp_name)
            log_message("export_to_excel exported temp csv {}".format(describe_file_state(csv_path)))
            data = normalize_table_data(read_csv_rows(csv_path, delimiter=delimiter, quotechar=text_qualifier))
            if not key_schedule:
                data = inject_element_id_column(data, view, csv_text=False)
            header_rows = get_section_row_count(view, DB.SectionType.Header)
            column_specs = get_column_specs(view)
            if key_schedule:
                # Preserve key schedule values exactly as exported (avoid numeric coercion).
                column_specs = None
            elif column_specs is not None:
                column_specs = [None] + column_specs
            write_table_to_sheet(
                sheet,
                data,
                1,
                header_rows=header_rows,
                column_specs=column_specs,
                doc=doc,
            )
            log_message("export_to_excel schedule complete name='{}' rows={}".format(view.Name, len(data)))
    finally:
        log_message("export_to_excel cleanup temp_dir='{}'".format(temp_dir))
        shutil.rmtree(temp_dir, ignore_errors=True)

    if "Sheet" in workbook.sheetnames and len(workbook.sheetnames) > 1:
        default_sheet = workbook["Sheet"]
        workbook.remove(default_sheet)

    workbook.save(file_path)
    log_message("export_to_excel saved workbook {}".format(describe_file_state(file_path)))
    return True


def export_to_csv(
    doc,
    schedules,
    folder,
    quote_all=False,
    delimiter=",",
    export_title=False,
    export_column_headers=True,
    export_group_headers=False,
    export_grouped_column_headers=False,
    text_qualifier="",
):
    log_message(
        "export_to_csv start folder='{}' schedules={} delimiter={!r} quotechar={!r} quote_all={} title={} col_headers={} group_headers={} grouped_col_headers={}".format(
            folder,
            len(schedules),
            delimiter,
            text_qualifier,
            bool(quote_all),
            bool(export_title),
            bool(export_column_headers),
            bool(export_group_headers),
            bool(export_grouped_column_headers),
        )
    )
    if not os.path.isdir(folder):
        os.makedirs(folder)
        log_message("export_to_csv created folder='{}'".format(folder))
    options = DB.ViewScheduleExportOptions()
    apply_schedule_export_options(
        options,
        delimiter=delimiter,
        export_title=export_title,
        export_column_headers=export_column_headers,
        export_group_headers=export_group_headers,
        export_grouped_column_headers=export_grouped_column_headers,
        text_qualifier=text_qualifier,
    )
    used_names = set()
    temp_dir = tempfile.mkdtemp(prefix="wwp_schedules_csv_")
    log_message("export_to_csv temp_dir='{}'".format(temp_dir))
    try:
        for view in schedules:
            log_message("export_to_csv schedule start name='{}' id={}".format(view.Name, element_id_value(view.Id)))
            key_schedule = is_key_schedule(view)
            base_name = sanitize_file_name(view.Name)
            unique_name = make_unique_name(base_name, used_names)
            file_name = "{}.csv".format(unique_name)
            temp_name = "tmp_{}_{}.csv".format(element_id_value(view.Id), unique_name)
            temp_csv_path = os.path.join(temp_dir, temp_name)
            final_csv_path = os.path.join(folder, file_name)
            log_message("export_to_csv view.Export temp_dir='{}' temp_name='{}'".format(temp_dir, temp_name))
            view.Export(temp_dir, temp_name, options)
            log_message("export_to_csv exported temp csv {}".format(describe_file_state(temp_csv_path)))
            rows = normalize_table_data(read_csv_rows(temp_csv_path, delimiter=delimiter, quotechar=text_qualifier))
            if not key_schedule:
                rows = inject_element_id_column(rows, view, csv_text=True)
            buffer_handle = io.StringIO()
            if text_qualifier in ("\"", "'"):
                writer = csv.writer(
                    buffer_handle,
                    delimiter=delimiter,
                    quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
                    quotechar=text_qualifier,
                )
            else:
                writer = csv.writer(
                    buffer_handle,
                    delimiter=delimiter,
                    quoting=csv.QUOTE_ALL if quote_all else csv.QUOTE_MINIMAL,
                )
            writer.writerows(rows)
            write_text_file(final_csv_path, buffer_handle.getvalue(), encoding="utf-8-sig")
            log_message(
                "export_to_csv schedule complete name='{}' temp={} output={}".format(
                    view.Name,
                    describe_file_state(temp_csv_path),
                    describe_file_state(final_csv_path),
                )
            )
    finally:
        log_message("export_to_csv cleanup temp_dir='{}'".format(temp_dir))
        shutil.rmtree(temp_dir, ignore_errors=True)
    return True


def select_csv_mode(ui, default_mode=0):
    options = [
        "Standard CSV (comma, minimal quotes)",
        "CSV (Quoted - all fields)",
    ]
    try:
        selected = ui.uiUtils_select_indices(
            options,
            title="CSV Export Mode",
            prompt="Choose CSV export format:",
            multiselect=False,
            width=520,
            height=260,
        )
    except Exception:
        selected = []
    if selected is None or len(selected) == 0:
        return None
    if selected[0] < 0 or selected[0] >= len(options):
        return default_mode
    return int(selected[0])


def select_csv_delimiter(ui, default_delimiter=","):
    options = [
        ("Comma (,)", ","),
        ("Semicolon (;)", ";"),
        ("Tab (\\t)", "\t"),
    ]
    labels = [opt[0] for opt in options]
    default_index = 0
    for idx, opt in enumerate(options):
        if opt[1] == default_delimiter:
            default_index = idx
            break
    try:
        selected = ui.uiUtils_select_indices(
            labels,
            title="CSV Delimiter",
            prompt="Choose delimiter:",
            multiselect=False,
            width=520,
            height=260,
        )
    except Exception:
        selected = []
    if selected is None or len(selected) == 0:
        return None
    sel_idx = int(selected[0]) if selected else default_index
    if sel_idx < 0 or sel_idx >= len(options):
        sel_idx = default_index
    return options[sel_idx][1]


def main():
    log_message("main start log_path='{}'".format(_log_file_path()))
    doc = get_active_doc()
    if doc is None:
        ui = load_uiutils()
        ui.uiUtils_alert("No active Revit document found.", title="Multiple Schedules Exporter")
        return
    config, save_config = get_config_and_saver()
    ui = load_uiutils()
    saved_sets_param = _ensure_saved_sets_param(doc, config, ui, save_config)
    proj_data = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param))
    project_settings = proj_data.get("settings", {}) if isinstance(proj_data, dict) else {}
    if not isinstance(project_settings, dict):
        project_settings = {}

    schedules = collect_schedules(doc)
    if not schedules:
        ui.uiUtils_alert("No schedules found.", title="Multiple Schedules Exporter")
        return

    items = [ScheduleItem(v) for v in schedules]
    log_message("main schedules loaded count={}".format(len(items)))
    last_ids = project_settings.get(CONFIG_LAST_SCHEDULE_IDS, config_get(config, CONFIG_LAST_SCHEDULE_IDS, []))
    try:
        prechecked_ids = set(int(x) for x in last_ids)
    except Exception:
        prechecked_ids = set()
    prechecked_indices = [
        idx for idx, item in enumerate(items)
        if element_id_value(item.view.Id) in prechecked_ids
    ]
    default_dir = get_default_dir(doc)
    last_excel_path = config_get(config, CONFIG_LAST_EXCEL_PATH, "")
    last_csv_dir = config_get(config, CONFIG_LAST_CSV_DIR, "")
    last_csv_mode = project_settings.get(CONFIG_LAST_CSV_MODE, config_get(config, CONFIG_LAST_CSV_MODE, 0))
    last_csv_delim = project_settings.get(CONFIG_LAST_CSV_DELIM, config_get(config, CONFIG_LAST_CSV_DELIM, ","))
    last_export_mode = project_settings.get(CONFIG_LAST_EXPORT_MODE, config_get(config, CONFIG_LAST_EXPORT_MODE, 0))
    last_export_title = project_settings.get(CONFIG_LAST_CSV_EXPORT_TITLE, config_get(config, CONFIG_LAST_CSV_EXPORT_TITLE, False))
    last_column_headers = project_settings.get(CONFIG_LAST_CSV_COLUMN_HEADERS, config_get(config, CONFIG_LAST_CSV_COLUMN_HEADERS, True))
    last_group_headers = project_settings.get(CONFIG_LAST_CSV_GROUP_HEADERS, config_get(config, CONFIG_LAST_CSV_GROUP_HEADERS, False))
    last_grouped_column_headers = project_settings.get(CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS, config_get(config, CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS, False))
    last_text_qualifier = project_settings.get(CONFIG_LAST_CSV_TEXT_QUALIFIER, config_get(config, CONFIG_LAST_CSV_TEXT_QUALIFIER, ""))
    last_use_category_sheet_name = project_settings.get(CONFIG_LAST_USE_CATEGORY_SHEET_NAME, config_get(config, CONFIG_LAST_USE_CATEGORY_SHEET_NAME, False))
    saved_sets = proj_data.get("sets", {})
    if not isinstance(saved_sets, dict):
        saved_sets = {}

    init_excel_path = last_excel_path or os.path.join(default_dir, "Schedules.xlsx")
    init_csv_dir = ensure_existing_dir(last_csv_dir, default_dir)

    default_settings = {
        "export_mode": last_export_mode,
        "export_title": bool(last_export_title),
        "export_column_headers": bool(last_column_headers),
        "export_group_headers": bool(last_group_headers),
        "export_grouped_column_headers": bool(last_grouped_column_headers),
        "use_category_sheet_name": True,
        "csv_mode": last_csv_mode,
        "csv_delim": last_csv_delim,
        "csv_text_qualifier": last_text_qualifier,
    }

    batch_result = _show_batch_dialog(
        saved_sets, doc,
        ui=ui,
        saved_sets_param=saved_sets_param,
        default_settings=default_settings,
    )
    if batch_result is None:
        return
    selected_batch_names, batch_override_paths, batch_settings = batch_result
    if not selected_batch_names:
        ui.uiUtils_alert("Select at least one set to export.", title="Multiple Schedules Exporter")
        return
    inputs = {"batch_sets": selected_batch_names, "batch_paths": batch_override_paths}
    if inputs is not False:
        if not inputs:
            return
        if "batch_sets" in inputs:
            batch_set_names = inputs["batch_sets"]
            batch_paths = inputs.get("batch_paths") or {}
            all_schedules_by_name = {v.Name: v for v in schedules}
            success_count = 0
            skipped = []
            warnings = []
            for set_name in batch_set_names:
                set_data = saved_sets.get(set_name)
                if not isinstance(set_data, dict):
                    skipped.append("{}: set not found".format(set_name))
                    continue
                set_mode = int(set_data.get("export_mode", 0))
                set_sched_names = set_data.get("schedule_names") or []
                set_views = [all_schedules_by_name[n] for n in set_sched_names if n in all_schedules_by_name]
                if not set_views:
                    skipped.append("{}: no matching schedules found in this model".format(set_name))
                    continue
                missing = [n for n in set_sched_names if n not in all_schedules_by_name]
                if missing:
                    sample = ", ".join(missing[:3]) + ("..." if len(missing) > 3 else "")
                    warnings.append("{}: {}/{} schedules not found (renamed?) -- {}".format(
                        set_name, len(set_views), len(set_sched_names), sample))
                set_use_cat = bool(batch_settings.get("use_category_sheet_name", True))
                bs = batch_settings
                set_exp_title = bool(set_data.get("export_title", bs.get("export_title", False)))
                set_col_hdr = bool(set_data.get("export_column_headers", bs.get("export_column_headers", True)))
                set_grp_hdr = bool(set_data.get("export_group_headers", bs.get("export_group_headers", False)))
                set_grpd_col = bool(set_data.get("export_grouped_column_headers", bs.get("export_grouped_column_headers", False)))
                set_delim = set_data.get("csv_delim", bs.get("csv_delim", ","))
                set_qualifier = set_data.get("csv_text_qualifier", bs.get("csv_text_qualifier", ""))
                if set_mode == 0:
                    set_excel_path = normalize_excel_output_path(batch_paths.get(set_name) or "")
                    if not set_excel_path:
                        skipped.append("{}: no output path set".format(set_name))
                        continue
                    ok = export_to_excel(
                        doc, set_views, set_excel_path, ui,
                        export_title=set_exp_title,
                        export_column_headers=set_col_hdr,
                        export_group_headers=set_grp_hdr,
                        export_grouped_column_headers=set_grpd_col,
                        text_qualifier=set_qualifier,
                        delimiter=set_delim,
                        use_category_sheet_name=set_use_cat,
                    )
                    if ok:
                        success_count += 1
                    else:
                        skipped.append("{}: export failed (see log)".format(set_name))
                else:
                    set_csv_folder = (batch_paths.get(set_name) or "").strip()
                    if not set_csv_folder:
                        skipped.append("{}: no output folder set".format(set_name))
                        continue
                    try:
                        export_to_csv(
                            doc, set_views, set_csv_folder,
                            quote_all=bool(int(set_data.get("csv_mode", 0)) == 1),
                            delimiter=set_delim,
                            export_title=set_exp_title,
                            export_column_headers=set_col_hdr,
                            export_group_headers=set_grp_hdr,
                            export_grouped_column_headers=set_grpd_col,
                            text_qualifier=set_qualifier,
                        )
                        success_count += 1
                    except Exception as _csv_exc:
                        log_exception("batch CSV export set '{}'".format(set_name), _csv_exc)
                        skipped.append("{}: export failed (see log)".format(set_name))
            paths_changed = False
            for sname, new_path in batch_paths.items():
                sdata = saved_sets.get(sname)
                if not isinstance(sdata, dict):
                    continue
                mode = int(sdata.get("export_mode", 0))
                if mode == 0:
                    if new_path and new_path != (sdata.get("excel_path") or "").strip():
                        sdata["excel_path"] = new_path
                        paths_changed = True
                else:
                    if new_path and new_path != (sdata.get("csv_folder") or "").strip():
                        sdata["csv_folder"] = new_path
                        paths_changed = True
            if paths_changed:
                proj_data_back = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param))
                proj_data_back["sets"] = saved_sets
                write_saved_sets(doc, proj_data_back, saved_sets_param)

            msg = "{} of {} set(s) exported successfully.".format(success_count, len(batch_set_names))
            if warnings:
                msg += "\n\nPartial exports (some schedules not found):\n" + "\n".join(warnings)
            if skipped:
                msg += "\n\nSkipped:\n" + "\n".join(skipped)
            ui.uiUtils_alert(msg, title="Multiple Schedules Exporter")
            return

        selected_indices = inputs.get("selected_indices") or []
        if not selected_indices:
            ui.uiUtils_alert("Select at least one schedule.", title="Multiple Schedules Exporter")
            return
        selected_views = [items[i].view for i in selected_indices]
        config.last_schedule_ids = [element_id_value(v.Id) for v in selected_views]
        export_title = bool(inputs.get("export_title"))
        export_column_headers = bool(inputs.get("export_column_headers"))
        export_group_headers = bool(inputs.get("export_group_headers"))
        export_grouped_column_headers = bool(inputs.get("export_grouped_column_headers"))
        csv_text_qualifier = inputs.get("csv_text_qualifier") or ""
        csv_delim = inputs.get("csv_delimiter") or last_csv_delim
        quote_all = bool(inputs.get("csv_quote_all"))
        use_category = bool(inputs.get("use_category_sheet_name", False))
        mode = int(inputs.get("mode", 0))
        if mode == 0:
            file_path = normalize_excel_output_path(inputs.get("excel_path"))
            if not file_path:
                ui.uiUtils_alert(
                    "Choose an Excel file path ending with .xlsx or .xlsm.",
                    title="Multiple Schedules Exporter",
                )
                return
            success = export_to_excel(
                doc,
                selected_views,
                file_path,
                ui,
                export_title=export_title,
                export_column_headers=export_column_headers,
                export_group_headers=export_group_headers,
                export_grouped_column_headers=export_grouped_column_headers,
                text_qualifier=csv_text_qualifier,
                delimiter=csv_delim,
                use_category_sheet_name=use_category,
            )
            if not success:
                return
            config.last_excel_path = _normalize_path(file_path)
            try:
                os.startfile(file_path)
            except Exception:
                pass
        else:
            folder = (inputs.get("csv_folder") or "").strip()
            if not folder:
                ui.uiUtils_alert("Choose a CSV folder.", title="Multiple Schedules Exporter")
                return
            export_to_csv(
                doc,
                selected_views,
                folder,
                quote_all=quote_all,
                delimiter=csv_delim,
                export_title=export_title,
                export_column_headers=export_column_headers,
                export_group_headers=export_group_headers,
                export_grouped_column_headers=export_grouped_column_headers,
                text_qualifier=csv_text_qualifier,
            )
            config.last_csv_dir = _normalize_path(folder)
        config.last_export_mode = mode
        config.last_csv_mode = 1 if quote_all else 0
        config.last_csv_delim = csv_delim
        config.last_csv_text_qualifier = csv_text_qualifier
        config.last_csv_export_title = export_title
        config.last_csv_column_headers = export_column_headers
        config.last_csv_group_headers = export_group_headers
        config.last_csv_grouped_column_headers = export_grouped_column_headers
        config.last_use_category_sheet_name = use_category
        proj_data = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param))
        proj_data["settings"] = {
            CONFIG_LAST_SCHEDULE_IDS: [element_id_value(v.Id) for v in selected_views],
            CONFIG_LAST_EXPORT_MODE: mode,
            CONFIG_LAST_CSV_MODE: 1 if quote_all else 0,
            CONFIG_LAST_CSV_DELIM: csv_delim,
            CONFIG_LAST_CSV_EXPORT_TITLE: export_title,
            CONFIG_LAST_CSV_COLUMN_HEADERS: export_column_headers,
            CONFIG_LAST_CSV_GROUP_HEADERS: export_group_headers,
            CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS: export_grouped_column_headers,
            CONFIG_LAST_CSV_TEXT_QUALIFIER: csv_text_qualifier,
            CONFIG_LAST_USE_CATEGORY_SHEET_NAME: use_category,
        }
        proj_data["sets"] = proj_data.get("sets", {})
        write_saved_sets(doc, proj_data, saved_sets_param)
        log_message("main saving config after modern dialog")
        save_config()
        ui.uiUtils_alert("Export complete.", title="Multiple Schedules Exporter")
        return

    if hasattr(ui, "uiUtils_select_items_with_mode"):
        selected_indices, mode = ui.uiUtils_select_items_with_mode(
            [item.display_name for item in items],
            title="Export Schedules",
            prompt="Select schedules to export:",
            mode_labels=("Export to Excel", "Export to CSV"),
            default_mode=0,
            prechecked_indices=prechecked_indices,
            width=680,
            height=620,
        )
    else:
        ui.uiUtils_alert(
            "UI helper uiUtils_select_items_with_mode is unavailable. Restart pyRevit or update WWP_uiUtils.",
            title="Multiple Schedules Exporter",
        )
        return
    if mode is None:
        return
    if not selected_indices:
        ui.uiUtils_alert("Select at least one schedule.", title="Multiple Schedules Exporter")
        return
    selected_views = [items[i].view for i in selected_indices]
    config.last_schedule_ids = [element_id_value(v.Id) for v in selected_views]

    if mode == 0:
        last_excel_dir = os.path.dirname(last_excel_path) if last_excel_path else ""
        init_dir = ensure_existing_dir(last_excel_dir, default_dir)
        file_name = os.path.basename(last_excel_path) if last_excel_path else "Schedules.xlsx"
        try:
            file_path = _pick_save_file(
                title="Export Schedules",
                filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                default_extension="xlsx",
                initial_directory=init_dir,
                file_name=file_name,
            )
        except Exception as exc:
            log_exception("Legacy native save dialog failed", exc)
            try:
                file_path = _pick_save_file(
                    title="Export Schedules",
                    filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                    default_extension="xlsx",
                    initial_directory="",
                    file_name=file_name,
                )
            except Exception as retry_exc:
                log_exception("Legacy native save dialog retry failed", retry_exc)
                ui.uiUtils_alert(
                    "Could not open the Excel save dialog. Check the suggested path and try again.",
                    title="Multiple Schedules Exporter",
                )
                return
        file_path = normalize_excel_output_path(file_path)
        if not file_path:
            ui.uiUtils_alert(
                "Choose an Excel file path ending with .xlsx or .xlsm.",
                title="Multiple Schedules Exporter",
            )
            return
        success = export_to_excel(doc, selected_views, file_path, ui)
        if not success:
            return
        config.last_excel_path = _normalize_path(file_path)
        try:
            os.startfile(file_path)
        except Exception:
            pass
    else:
        init_dir = ensure_existing_dir(last_csv_dir, default_dir)
        csv_mode = select_csv_mode(ui, default_mode=last_csv_mode)
        if csv_mode is None:
            return
        csv_delim = select_csv_delimiter(ui, default_delimiter=last_csv_delim)
        if csv_delim is None:
            return
        folder = _pick_folder(
            title="Select CSV Folder",
            initial_directory=init_dir,
        )
        if not folder:
            return
        export_to_csv(doc, selected_views, folder, quote_all=(csv_mode == 1), delimiter=csv_delim)
        config.last_csv_dir = _normalize_path(folder)
        config.last_csv_mode = csv_mode
        config.last_csv_delim = csv_delim

    log_message("main saving config after legacy dialog")
    proj_data = _normalize_namespace_data(read_saved_sets(doc, saved_sets_param))
    proj_data["settings"] = {
        CONFIG_LAST_SCHEDULE_IDS: [element_id_value(v.Id) for v in selected_views],
        CONFIG_LAST_EXPORT_MODE: mode,
        CONFIG_LAST_CSV_MODE: config_get(config, CONFIG_LAST_CSV_MODE, 0),
        CONFIG_LAST_CSV_DELIM: config_get(config, CONFIG_LAST_CSV_DELIM, ","),
        CONFIG_LAST_CSV_EXPORT_TITLE: config_get(config, CONFIG_LAST_CSV_EXPORT_TITLE, False),
        CONFIG_LAST_CSV_COLUMN_HEADERS: config_get(config, CONFIG_LAST_CSV_COLUMN_HEADERS, True),
        CONFIG_LAST_CSV_GROUP_HEADERS: config_get(config, CONFIG_LAST_CSV_GROUP_HEADERS, False),
        CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS: config_get(config, CONFIG_LAST_CSV_GROUPED_COLUMN_HEADERS, False),
        CONFIG_LAST_CSV_TEXT_QUALIFIER: config_get(config, CONFIG_LAST_CSV_TEXT_QUALIFIER, ""),
    }
    proj_data["sets"] = proj_data.get("sets", {})
    write_saved_sets(doc, proj_data, saved_sets_param)
    save_config()
    ui.uiUtils_alert("Export complete.", title="Multiple Schedules Exporter")


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log_exception("Unhandled exception in Export2Ex", ex)
        show_error_report(ex)
        raise
