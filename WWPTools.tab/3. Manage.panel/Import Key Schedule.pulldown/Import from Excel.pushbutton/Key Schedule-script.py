#!python3
import clr
import json
import os
import re
import sys
from difflib import SequenceMatcher

from pyrevit import DB
from WWP_settings import get_tool_settings


TITLE = "Import Key Schedule from Excel"
SKIP_OPTION = "(Skip)"
KEY_NAME_OPTION = "Key Name"
TARGET_OPTIONS = ["Area Key Schedule", "Room Key Schedule"]
DEFAULT_SCHEDULE_SUFFIX = "Key Schedule - Imported"
_CONFIG_CACHE = None
PROJECT_SETTINGS_PARAM = "! P_STATS_KeyScheduleMap"
PROJECT_SETTINGS_NAMESPACE = "import_key_schedule"
PROJECT_SETTINGS_SAVED_SETS_KEY = "saved_sets"
SAVED_SETTING_SETS_KEY = "area_key_import_saved_setting_sets_json"
SAVED_SETTING_FILE_FILTER = "WWPTools Import Key Schedule Settings (*.json)|*.json|All Files (*.*)|*.*"
LEGACY_SAVED_SETTING_SCHEMA_GUID = "79c6f037-2a4b-4b5d-8ab8-fb699f0785af"
LEGACY_SAVED_SETTING_SCHEMA_FIELD = "SavedSetsJson"
STANDARD_HEADER_PARAMETER_ALIASES = {
    "name": ["Name"],
    "unitcount": ["! S_STATS_Counts_Number", "S_STATS_Counts_Number"],
    "unit count": ["! S_STATS_Counts_Number", "S_STATS_Counts_Number"],
    "program": ["! P_STATS_Program_Text", "P_STATS_Program_Text"],
    "gca": ["! P_STATS_Area/Room_GCA_YesNo", "P_STATS_Area/Room_GCA_YesNo"],
    "gfa": ["! P_STATS_Area/Room_GFA_YesNo", "P_STATS_Area/Room_GFA_YesNo"],
    "gfa by law": ["! P_STATS_Area/Room_GFA_YesNo", "P_STATS_Area/Room_GFA_YesNo"],
    "gfa bylaw": ["! P_STATS_Area/Room_GFA_YesNo", "P_STATS_Area/Room_GFA_YesNo"],
    "nsa": ["! P_STATS_Area/Room_NSA_YesNo", "P_STATS_Area/Room_NSA_YesNo"],
    "display category": ["! P_STATS_Area/Room_GFACategory_Text", "P_STATS_Area/Room_GFACategory_Text"],
    "gfa category": ["! P_STATS_Area/Room_GFACategory_Text", "P_STATS_Area/Room_GFACategory_Text"],
    "color": ["! P_STATS_Area/Room_Fill Colour_Text", "P_STATS_Area/Room_Fill Colour_Text"],
    "colour": ["! P_STATS_Area/Room_Fill Colour_Text", "P_STATS_Area/Room_Fill Colour_Text"],
    "fill color": ["! P_STATS_Area/Room_Fill Colour_Text", "P_STATS_Area/Room_Fill Colour_Text"],
    "fill colour": ["! P_STATS_Area/Room_Fill Colour_Text", "P_STATS_Area/Room_Fill Colour_Text"],
    "boma area category": [
        "! P_STATS_Area/Room_BOMAAreaCategory_Text",
        "P_STATS_Area/Room_BOMAAreaCategory_Text",
        "! P_STATS_Area/Room_BOMA Area Category_Text",
        "P_STATS_Area/Room_BOMA Area Category_Text",
        "! P_STATS_Area/Room_BOMACategory_Text",
        "P_STATS_Area/Room_BOMACategory_Text",
    ],
}


def _load_local_window_xaml(xaml_name):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows.Markup import XamlReader
    from System.IO import StringReader
    from System.Xml import XmlReader

    xaml_path = os.path.join(os.path.dirname(__file__), xaml_name)
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))
    with open(xaml_path, "r") as f:
        xaml_text = f.read()
    reader = XmlReader.Create(StringReader(xaml_text))
    return XamlReader.Load(reader)


def _get_owner_handle():
    try:
        clr.AddReference("System")
        from System.Diagnostics import Process
        return Process.GetCurrentProcess().MainWindowHandle
    except Exception:
        try:
            from System import IntPtr
            return IntPtr.Zero
        except Exception:
            return None


def _find_first_visual_child(parent, target_type):
    from System.Windows.Media import VisualTreeHelper

    if parent is None:
        return None
    try:
        count = VisualTreeHelper.GetChildrenCount(parent)
    except Exception:
        return None
    for i in range(count):
        try:
            child = VisualTreeHelper.GetChild(parent, i)
        except Exception:
            continue
        try:
            if isinstance(child, target_type):
                return child
        except Exception:
            pass
        result = _find_first_visual_child(child, target_type)
        if result is not None:
            return result
    return None


def _useful_selection_count(selections):
    count = 0
    for item in selections or []:
        if item and item != SKIP_OPTION:
            count += 1
    return count


def _show_area_keyplan_import_dialog(
    title="Import Area Key Schedule",
    file_path="",
    column_names=None,
    target_types=None,
    selected_target_type="",
    parameter_options=None,
    default_selections=None,
    auto_map_selections=None,
    saved_setting_sets=None,
    save_saved_setting_sets=None,
    width=980,
    height=720,
):
    from System import String
    from System.Collections import ArrayList
    from System.Collections.Generic import List
    from System.Windows.Controls import ComboBox as WpfComboBox
    from System.Windows.Interop import WindowInteropHelper
    from System.Windows import MessageBox

    window = _load_local_window_xaml("AreaKeyplanImportWindow.xaml")
    window.Title = title or "Import Area Key Schedule"
    if width > 0:
        window.Width = width
    if height > 0:
        window.Height = height

    try:
        from System import IntPtr
        owner = _get_owner_handle()
        if owner and owner != IntPtr.Zero:
            WindowInteropHelper(window).Owner = owner
    except Exception:
        pass

    file_path_box = window.FindName("FilePathBox")
    browse_button = window.FindName("BrowseButton")
    load_button = window.FindName("LoadButton")
    target_combo = window.FindName("TargetTypeCombo")
    mapping_grid = window.FindName("MappingGrid")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    saved_set_combo = window.FindName("SavedSetCombo")
    load_set_button = window.FindName("LoadSetButton")
    save_set_button = window.FindName("SaveSetButton")
    delete_set_button = window.FindName("DeleteSetButton")
    export_set_button = window.FindName("ExportSetButton")
    import_set_button = window.FindName("ImportSetButton")

    if file_path_box is not None:
        file_path_box.Text = file_path or ""

    target_types = [str(t) for t in (target_types or [])]
    if target_combo is not None:
        for item in target_types:
            target_combo.Items.Add(item)
        if selected_target_type and selected_target_type in target_types:
            target_combo.SelectedItem = selected_target_type
        elif target_combo.Items.Count > 0:
            target_combo.SelectedIndex = 0

    options = [str(o) for o in (parameter_options or [])]
    columns = [str(c) for c in (column_names or [])]
    defaults = [str(d) for d in (default_selections or [])]
    auto_defaults = [str(d) for d in (auto_map_selections or [])]
    if columns:
        option_param_names = [o for o in options if o not in (SKIP_OPTION, KEY_NAME_OPTION)]
        if not auto_defaults or len(auto_defaults) != len(columns) or _useful_selection_count(auto_defaults) == 0:
            auto_defaults = build_default_selections([_strip_excel_column_prefix(c) for c in columns], option_param_names)
        if len(defaults) != len(columns) or _useful_selection_count(defaults) == 0:
            defaults = list(auto_defaults)
    saved_sets = saved_setting_sets if isinstance(saved_setting_sets, dict) else {}
    net_options = List[String]()
    for option in options:
        net_options.Add(option)

    class _ColumnMapping(object):
        def __init__(self, col, sel):
            self.ColumnName = col
            self.SelectedOption = sel

    def _normalize_mapped_selection(index, selections):
        sel = selections[index] if index < len(selections) else ""
        if not sel or sel not in options:
            sel = options[0] if options else ""
        return sel

    mappings = ArrayList()
    for i, col in enumerate(columns):
        mappings.Add(_ColumnMapping(col, _normalize_mapped_selection(i, defaults)))

    def _show_message(message):
        try:
            MessageBox.Show(window, message, title)
        except Exception:
            pass

    if mapping_grid is not None:
        mapping_grid.Tag = net_options
        mapping_grid.ItemsSource = mappings
        has_data = mappings.Count > 0
        mapping_grid.IsEnabled = has_data
        if ok_button is not None:
            ok_button.IsEnabled = has_data

    result_state = {"load_requested": False}

    def _refresh_saved_set_combo(selected_name=None):
        if saved_set_combo is None:
            return
        current = selected_name if selected_name is not None else str(saved_set_combo.Text or "")
        saved_set_combo.Items.Clear()
        for name in sorted(saved_sets.keys()):
            saved_set_combo.Items.Add(name)
        if current:
            saved_set_combo.Text = current

    def _current_target_type():
        return str(target_combo.SelectedItem or "") if target_combo is not None else ""

    def _current_mapping_payload():
        _sync_mapping_grid_combos()
        items = []
        if mapping_grid is not None and mapping_grid.ItemsSource is not None:
            for i in range(mapping_grid.Items.Count):
                item = mapping_grid.Items[i]
                label = str(getattr(item, "ColumnName", "") or "")
                header = _strip_excel_column_prefix(label)
                option = str(getattr(item, "SelectedOption", "") or "")
                items.append(
                    {
                        "index": i,
                        "column": label,
                        "header": header,
                        "option": option,
                    }
                )
        return {
            "version": 1,
            "target_type": _current_target_type(),
            "mappings": items,
        }

    def _save_saved_sets():
        if callable(save_saved_setting_sets):
            try:
                save_saved_setting_sets(saved_sets)
            except Exception:
                pass

    def _apply_setting_set(set_data):
        if not isinstance(set_data, dict):
            _show_message("Saved set data is not valid.")
            return
        mappings_data = set_data.get("mappings") or []
        by_header = {}
        by_column = {}
        by_index = {}
        for entry in mappings_data:
            if not isinstance(entry, dict):
                continue
            option = str(entry.get("option") or "")
            if option not in options:
                continue
            header_key = _normalize_name(_strip_excel_column_prefix(entry.get("header") or ""))
            column_key = _normalize_name(entry.get("column") or "")
            if header_key:
                by_header[header_key] = option
            if column_key:
                by_column[column_key] = option
            try:
                by_index[int(entry.get("index"))] = option
            except Exception:
                pass

        applied = 0
        if mapping_grid is not None and mapping_grid.ItemsSource is not None:
            for i in range(mapping_grid.Items.Count):
                item = mapping_grid.Items[i]
                label = str(getattr(item, "ColumnName", "") or "")
                header_key = _normalize_name(_strip_excel_column_prefix(label))
                column_key = _normalize_name(label)
                option = by_header.get(header_key) or by_column.get(column_key) or by_index.get(i)
                if option and option in options:
                    item.SelectedOption = option
                    applied += 1
        _sync_mapping_grid_combos()
        if applied == 0 and mappings_data:
            _show_message("Saved set loaded, but none of its mapped parameters are available for the current target.")

    def _selected_set_name():
        return str(saved_set_combo.Text or "").strip() if saved_set_combo is not None else ""

    def on_load_set(sender, e):
        name = _selected_set_name()
        if not name or name not in saved_sets:
            _show_message("Select a saved set to load.")
            return
        _apply_setting_set(saved_sets[name])

    def on_save_set(sender, e):
        name = _selected_set_name()
        if not name:
            _show_message("Type a saved set name before saving.")
            return
        saved_sets[name] = _current_mapping_payload()
        _save_saved_sets()
        _refresh_saved_set_combo(name)

    def on_delete_set(sender, e):
        name = _selected_set_name()
        if not name or name not in saved_sets:
            _show_message("Select a saved set to delete.")
            return
        del saved_sets[name]
        _save_saved_sets()
        _refresh_saved_set_combo("")

    def on_export_set(sender, e):
        name = _selected_set_name()
        if not name or name not in saved_sets:
            _show_message("Select a saved set to export.")
            return
        from Microsoft.Win32 import SaveFileDialog
        dlg = SaveFileDialog()
        dlg.Title = "Export Key Schedule Setting"
        dlg.Filter = SAVED_SETTING_FILE_FILTER
        dlg.DefaultExt = "json"
        dlg.FileName = "{}.json".format(re.sub(r"[^A-Za-z0-9_. -]+", "_", name).strip(" .") or "ImportKeyScheduleSetting")
        if dlg.ShowDialog() == True:
            data = {
                "tool": "WWPTools Import Key Schedule",
                "version": 1,
                "name": name,
                "setting": saved_sets[name],
            }
            try:
                with open(dlg.FileName, "w") as f:
                    json.dump(data, f, indent=2, sort_keys=True)
            except Exception as exc:
                _show_message("Could not export setting file.\n{}".format(exc))

    def on_import_set(sender, e):
        from Microsoft.Win32 import OpenFileDialog
        dlg = OpenFileDialog()
        dlg.Title = "Import Key Schedule Setting"
        dlg.Filter = SAVED_SETTING_FILE_FILTER
        dlg.CheckFileExists = True
        if dlg.ShowDialog() != True:
            return
        try:
            with open(dlg.FileName, "r") as f:
                data = json.load(f)
        except Exception as exc:
            _show_message("Could not read setting file.\n{}".format(exc))
            return
        name = str(data.get("name") or os.path.splitext(os.path.basename(dlg.FileName))[0]).strip()
        setting = data.get("setting") if isinstance(data, dict) else None
        if not isinstance(setting, dict):
            _show_message("Setting file is not valid.")
            return
        saved_sets[name] = setting
        _save_saved_sets()
        _refresh_saved_set_combo(name)
        _apply_setting_set(setting)

    def _sync_mapping_grid_combos():
        if mapping_grid is None or mapping_grid.ItemsSource is None:
            return
        try:
            mapping_grid.UpdateLayout()
        except Exception:
            pass
        for i in range(mapping_grid.Items.Count):
            try:
                item = mapping_grid.Items[i]
            except Exception:
                continue
            try:
                row = mapping_grid.ItemContainerGenerator.ContainerFromIndex(i)
            except Exception:
                row = None
            if row is None:
                continue
            combo = _find_first_visual_child(row, WpfComboBox)
            if combo is None:
                continue
            try:
                combo.ItemsSource = net_options
            except Exception:
                pass
            selected_value = str(getattr(item, "SelectedOption", "") or "")
            try:
                combo.SelectedItem = selected_value
            except Exception:
                pass
            if (not str(combo.SelectedItem or "")) and options:
                try:
                    combo.SelectedIndex = 0
                except Exception:
                    pass
            try:
                item.SelectedOption = str(combo.SelectedItem or "")
            except Exception:
                pass

    def on_browse(sender, e):
        from Microsoft.Win32 import OpenFileDialog
        dlg = OpenFileDialog()
        dlg.Title = "Select Excel File"
        dlg.Filter = "Excel Files (*.xlsx)|*.xlsx|All Files (*.*)|*.*"
        dlg.CheckFileExists = True
        if dlg.ShowDialog() == True:
            file_path_box.Text = dlg.FileName or ""

    def on_ok(sender, e):
        result_state["load_requested"] = False
        window.DialogResult = True

    def on_load(sender, e):
        result_state["load_requested"] = True
        window.DialogResult = True

    def on_cancel(sender, e):
        window.DialogResult = False

    def on_content_rendered(sender, e):
        _sync_mapping_grid_combos()

    if browse_button is not None:
        browse_button.Click += on_browse
    if ok_button is not None:
        ok_button.Click += on_ok
    if load_button is not None:
        load_button.Click += on_load
    if cancel_button is not None:
        cancel_button.Click += on_cancel
    if load_set_button is not None:
        load_set_button.Click += on_load_set
    if save_set_button is not None:
        save_set_button.Click += on_save_set
    if delete_set_button is not None:
        delete_set_button.Click += on_delete_set
    if export_set_button is not None:
        export_set_button.Click += on_export_set
    if import_set_button is not None:
        import_set_button.Click += on_import_set
    _refresh_saved_set_combo()
    window.ContentRendered += on_content_rendered

    if window.ShowDialog() != True:
        return None

    result_target_type = str(target_combo.SelectedItem or "") if target_combo is not None else ""
    result_file_path = str(file_path_box.Text or "") if file_path_box is not None else ""
    selected_options = []
    column_names_out = []

    if mapping_grid is not None and mapping_grid.ItemsSource is not None:
        _sync_mapping_grid_combos()
        for i in range(mapping_grid.Items.Count):
            item = mapping_grid.Items[i]
            column_names_out.append(str(getattr(item, "ColumnName", "") or ""))
            sel = str(getattr(item, "SelectedOption", "") or "")
            try:
                row = mapping_grid.ItemContainerGenerator.ContainerFromIndex(i)
            except Exception:
                row = None
            if row is not None:
                combo = _find_first_visual_child(row, WpfComboBox)
                if combo is not None:
                    sel = str(combo.SelectedItem or "")
                    try:
                        item.SelectedOption = sel
                    except Exception:
                        pass
            selected_options.append(sel)

    return {
        "load_requested": result_state["load_requested"],
        "selected_target_type": result_target_type,
        "file_path": result_file_path,
        "column_names": column_names_out,
        "selected_options": selected_options,
    }




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def _get_config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    _CONFIG_CACHE, _ = get_tool_settings(
        "ImportAreaKeySchedule",
        doc=get_revit_doc(),
    )
    return _CONFIG_CACHE


def get_revit_doc():
    uidoc = getattr(__revit__, "ActiveUIDocument", None)
    if uidoc is None:
        return None
    return getattr(uidoc, "Document", None)


class RevitTransaction(object):
    def __init__(self, doc, name):
        self._transaction = DB.Transaction(doc, name)

    def __enter__(self):
        self._transaction.Start()
        return self._transaction

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._transaction.Commit()
        else:
            try:
                self._transaction.RollBack()
            except Exception:
                pass
        return False


def _safe_config_get(config, key, default=None):
    if config is None:
        return default
    try:
        return getattr(config, key, default)
    except Exception:
        return default


def _safe_config_set(config, key, value):
    if config is None:
        return
    try:
        setattr(config, key, value)
    except Exception:
        pass


def _save_config():
    try:
        config = _get_config()
        if config is not None:
            config.save()
    except Exception:
        pass


def _parse_saved_setting_sets(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for name, setting in data.items():
        if not name or not isinstance(setting, dict):
            continue
        cleaned[str(name)] = setting
    return cleaned


def _get_project_settings_param(doc):
    try:
        project_info = getattr(doc, "ProjectInformation", None)
        if project_info is None:
            return None
        return project_info.LookupParameter(PROJECT_SETTINGS_PARAM)
    except Exception:
        return None


def _read_project_settings_payload(doc):
    param = _get_project_settings_param(doc)
    if param is None:
        return {}
    try:
        raw = param.AsString() or ""
    except Exception:
        raw = ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _write_project_settings_payload(doc, payload):
    param = _get_project_settings_param(doc)
    if param is None:
        return False
    try:
        if param.IsReadOnly:
            return False
    except Exception:
        pass
    try:
        raw = json.dumps(payload or {}, sort_keys=True)
    except Exception:
        return False

    transaction = DB.Transaction(doc, "Save WWPTools Project Settings")
    transaction.Start()
    try:
        param.Set(raw)
        transaction.Commit()
        return True
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return False


def _read_saved_setting_sets_from_project_param(doc):
    payload = _read_project_settings_payload(doc)
    namespace_data = payload.get(PROJECT_SETTINGS_NAMESPACE)
    if not isinstance(namespace_data, dict):
        return {}
    return _parse_saved_setting_sets(namespace_data.get(PROJECT_SETTINGS_SAVED_SETS_KEY))


def _write_saved_setting_sets_to_project_param(doc, saved_sets):
    payload = _read_project_settings_payload(doc)
    if not isinstance(payload, dict):
        payload = {}
    namespace_data = payload.get(PROJECT_SETTINGS_NAMESPACE)
    if not isinstance(namespace_data, dict):
        namespace_data = {}
    namespace_data[PROJECT_SETTINGS_SAVED_SETS_KEY] = saved_sets or {}
    payload[PROJECT_SETTINGS_NAMESPACE] = namespace_data
    return _write_project_settings_payload(doc, payload)


def _read_legacy_saved_setting_sets_from_doc(doc):
    try:
        from System import Guid, String
        project_info = getattr(doc, "ProjectInformation", None)
        if project_info is None:
            return {}
        schema = DB.ExtensibleStorage.Schema.Lookup(Guid(LEGACY_SAVED_SETTING_SCHEMA_GUID))
        if schema is None:
            return {}
        entity = project_info.GetEntity(schema)
        if entity is None or not entity.IsValid():
            return {}
        raw = entity.Get[String](LEGACY_SAVED_SETTING_SCHEMA_FIELD) or ""
        return _parse_saved_setting_sets(raw)
    except Exception:
        return {}


def _load_saved_setting_sets(doc, config):
    doc_sets = _read_saved_setting_sets_from_project_param(doc)
    if doc_sets:
        return doc_sets
    legacy_doc_sets = _read_legacy_saved_setting_sets_from_doc(doc)
    if legacy_doc_sets:
        _write_saved_setting_sets_to_project_param(doc, legacy_doc_sets)
        return legacy_doc_sets
    raw = _safe_config_get(config, SAVED_SETTING_SETS_KEY, "") or ""
    local_sets = _parse_saved_setting_sets(raw)
    if local_sets:
        _write_saved_setting_sets_to_project_param(doc, local_sets)
    return local_sets


def _write_saved_setting_sets(doc, config, saved_sets):
    wrote_doc = _write_saved_setting_sets_to_project_param(doc, saved_sets)
    if config is None:
        return wrote_doc
    try:
        payload = json.dumps(saved_sets or {}, sort_keys=True)
    except Exception:
        payload = "{}"
    _safe_config_set(config, SAVED_SETTING_SETS_KEY, payload)
    _save_config()
    return wrote_doc


def _header_signature(headers):
    return "|".join([_normalize_name(h) for h in headers or []])


def _mapping_signature_key(category_key):
    return "area_key_import_headers_signature_{}".format(category_key)


def _mapping_selection_key(category_key):
    return "area_key_import_selected_options_{}".format(category_key)


def _target_category_key(selected_target_type):
    target = resolve_schedule_target(selected_target_type)
    return _normalize_name(target.get("label"))


def _load_saved_mapping(config, category_key, signature, column_count, parameter_options, auto_defaults=None):
    saved_signature = _safe_config_get(config, _mapping_signature_key(category_key), "") or ""
    saved_selections = _safe_config_get(config, _mapping_selection_key(category_key), None)
    defaults = None
    if saved_signature == signature:
        defaults = _sanitize_selections(saved_selections, column_count, parameter_options)
    if defaults is not None:
        return _merge_defaults_with_saved(auto_defaults, defaults)

    legacy_signature = _safe_config_get(config, "area_key_import_headers_signature", "") or ""
    legacy_target = _safe_config_get(config, "area_key_import_target", "") or ""
    legacy_target_key = _target_category_key(legacy_target) if legacy_target else ""
    legacy_selections = _safe_config_get(config, "area_key_import_selected_options", None)
    if legacy_signature == signature and legacy_target_key == category_key:
        defaults = _sanitize_selections(legacy_selections, column_count, parameter_options)
        return _merge_defaults_with_saved(auto_defaults, defaults)
    return None


def _merge_defaults_with_saved(auto_defaults, saved_defaults):
    if not auto_defaults:
        return saved_defaults
    if not saved_defaults or len(saved_defaults) != len(auto_defaults):
        return list(auto_defaults)
    merged = []
    for index, auto_value in enumerate(auto_defaults):
        saved_value = saved_defaults[index]
        if auto_value and auto_value != SKIP_OPTION:
            merged.append(auto_value)
        else:
            merged.append(saved_value)
    return merged


def _sanitize_selections(selections, column_count, parameter_options):
    if not selections or len(selections) != column_count:
        return None
    valid = set(parameter_options)
    cleaned = []
    useful_count = 0
    for item in selections:
        if item in valid:
            cleaned.append(item)
            if item != SKIP_OPTION:
                useful_count += 1
        else:
            cleaned.append(SKIP_OPTION)
    if useful_count == 0:
        return None
    return cleaned


def _default_schedule_name(file_path, category_label):
    default_name = "{} {}".format(category_label, DEFAULT_SCHEDULE_SUFFIX)
    if file_path:
        try:
            base = os.path.splitext(os.path.basename(file_path))[0]
            if base:
                return base
        except Exception:
            pass
    return default_name


def _is_bic_value(cat_id, bic):
    if not cat_id:
        return False
    try:
        return _elem_id_int(cat_id) == int(bic)
    except Exception:
        return False


def resolve_schedule_target(selected_target_type):
    if _normalize_name(selected_target_type) == _normalize_name(TARGET_OPTIONS[1]):
        return {"bic": DB.BuiltInCategory.OST_Rooms, "label": "Room"}
    return {"bic": DB.BuiltInCategory.OST_Areas, "label": "Area"}


def add_lib_path():
    script_dir = os.path.dirname(__file__)
    lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
    if lib_path not in sys.path:
        sys.path.append(lib_path)


def load_uiutils():
    add_lib_path()
    import WWP_uiUtils as ui
    return ui


def unique_view_name(doc, base_name):
    existing = set(v.Name for v in DB.FilteredElementCollector(doc).OfClass(DB.View))
    if base_name not in existing:
        return base_name
    index = 1
    while True:
        candidate = "{} ({})".format(base_name, index)
        if candidate not in existing:
            return candidate
        index += 1


def element_id_value(elem_id):
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


def get_category_parameter_options(doc, category_bic):
    params = {}
    elements = (
        DB.FilteredElementCollector(doc)
        .OfCategory(category_bic)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    sample = elements[0] if elements else None
    if sample:
        for param in sample.Parameters:
            if not param:
                continue
            try:
                if param.IsReadOnly:
                    continue
            except Exception:
                pass
            try:
                storage_type = param.StorageType
                none_type = getattr(DB.StorageType, "None")
                if storage_type == none_type:
                    continue
                if storage_type == DB.StorageType.ElementId:
                    continue
            except Exception:
                continue
            try:
                name = param.Definition.Name
            except Exception:
                continue
            if not name:
                continue
            param_id = param.Id
            if name in params and params[name] != param_id:
                existing_id = params.get(name)
                if existing_id is not None:
                    existing_display = "{} (Id {})".format(name, element_id_value(existing_id))
                    if existing_display not in params:
                        params[existing_display] = existing_id
                    if name in params:
                        params.pop(name, None)
                display_name = "{} (Id {})".format(name, element_id_value(param_id))
            else:
                display_name = name
            params[display_name] = param_id

    if not params:
        params = _get_schedulable_parameter_options(doc, category_bic)

    display_names = sorted(params.keys())
    return display_names, params


def _get_schedulable_parameter_options(doc, category_bic):
    params = {}
    transaction = DB.Transaction(doc, TITLE + " Parameter Probe")
    try:
        transaction.Start()
        schedule, _ = create_key_schedule(doc, "__WWP_TEMP_KEY_SCHEDULE__", category_bic, "Temporary")
        if schedule is None or not schedule.Definition:
            return {}

        definition = schedule.Definition
        try:
            schedulable_fields = list(definition.GetSchedulableFields())
        except Exception:
            schedulable_fields = []

        for schedulable_field in schedulable_fields:
            try:
                field = definition.AddField(schedulable_field)
            except Exception:
                continue
            if not field:
                continue
            try:
                name = field.GetName() or ""
            except Exception:
                name = ""
            name = name.strip()
            if not name or _normalize_name(name) in ("key", "key name", "keyname"):
                continue
            try:
                param_id = field.ParameterId
            except Exception:
                param_id = None
            if not param_id:
                continue

            if name in params and params[name] != param_id:
                existing_id = params.get(name)
                if existing_id is not None:
                    existing_display = "{} (Id {})".format(name, element_id_value(existing_id))
                    if existing_display not in params:
                        params[existing_display] = existing_id
                    params.pop(name, None)
                display_name = "{} (Id {})".format(name, element_id_value(param_id))
            else:
                display_name = name
            params[display_name] = param_id
    except Exception:
        params = {}
    finally:
        try:
            if transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            try:
                transaction.RollBack()
            except Exception:
                pass
    return params


def read_workbook(path, ui):
    add_lib_path()
    try:
        import openpyxl
    except Exception as exc:
        ui.uiUtils_alert("openpyxl is not available.\n{}".format(exc), title=TITLE)
        return None
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:
        ui.uiUtils_alert("Failed to open workbook.\n{}".format(exc), title=TITLE)
        return None


def _excel_column_letter(index):
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def extract_excel_data(workbook):
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    if not rows:
        return [], [], []

    max_col = 0
    for row in rows:
        if row is None:
            continue
        for idx in range(len(row) - 1, -1, -1):
            val = row[idx]
            if val not in (None, ""):
                if idx + 1 > max_col:
                    max_col = idx + 1
                break
        if len(row) > max_col:
            max_col = len(row)
    if max_col == 0:
        return [], [], []

    header_row = list(rows[0]) if rows else []
    headers = []
    column_labels = []
    for idx in range(max_col):
        raw = header_row[idx] if idx < len(header_row) else None
        header = str(raw).strip() if raw is not None else ""
        headers.append(header)
        letter = _excel_column_letter(idx + 1)
        label = letter
        if header:
            label = "{} - {}".format(letter, header)
        column_labels.append(label)

    data_rows = []
    for raw_row in rows[1:]:
        if raw_row is None:
            continue
        row = list(raw_row) + [None] * (max_col - len(raw_row))
        if all(cell in (None, "") for cell in row):
            continue
        data_rows.append(row)

    return headers, column_labels, data_rows


def format_preview_value(value):
    if value is None:
        return ""
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return ""


def build_preview_lines(headers, rows, max_rows=8):
    lines = []
    if headers:
        lines.append(" | ".join([h or "" for h in headers]))
    for row in rows[:max_rows]:
        line = " | ".join([format_preview_value(v) for v in row])
        lines.append(line)
    return lines


def _base_parameter_name(name):
    raw = (name or "").strip()
    if raw.endswith(")") and " (id " in _normalize_name(raw):
        return raw.rsplit(" (", 1)[0].strip()
    return raw


def _strip_excel_column_prefix(value):
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^[A-Za-z]{1,3}\s*-\s*(.+)$", raw)
    if match:
        return match.group(1).strip()
    return raw


def _alias_key(value):
    raw = _strip_excel_column_prefix(value)
    text = raw.replace("&", " and ")
    text = re.sub(r"[_\-/]+", " ", text)
    text = re.sub(r"[^a-zA-Z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _match_text_variants(value):
    source_values = []
    raw = (value or "").strip()
    stripped = _strip_excel_column_prefix(raw)
    for source in (raw, stripped):
        normalized = _normalize_name(source)
        if normalized and normalized not in source_values:
            source_values.append(normalized)

    results = []
    seen = set()
    for source in source_values:
        text = source.replace("&", " and ")
        text = re.sub(r"[_\-/]+", " ", text)
        text = re.sub(r"[^a-z0-9\s]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        compact = text.replace(" ", "")
        tokens = [token for token in text.split(" ") if token]
        singular_tokens = []
        for token in tokens:
            if len(token) > 3 and token.endswith("s"):
                singular_tokens.append(token[:-1])
            else:
                singular_tokens.append(token)
        singular_text = " ".join(singular_tokens)
        singular_compact = singular_text.replace(" ", "")
        variants = [text, compact, singular_text, singular_compact]
        for variant in variants:
            if variant and variant not in seen:
                seen.add(variant)
                results.append(variant)
    return results


def _build_parameter_candidates(param_display_names):
    candidates = []
    for name in param_display_names or []:
        raw = (name or "").strip()
        if not raw:
            continue
        base = _base_parameter_name(raw)
        variants = []
        variants.extend(_match_text_variants(raw))
        if base != raw:
            variants.extend(_match_text_variants(base))
        seen = set()
        unique_variants = []
        for variant in variants:
            if variant and variant not in seen:
                seen.add(variant)
                unique_variants.append(variant)
        token_set = set()
        compact_variants = set()
        for variant in unique_variants:
            compact_variant = variant.replace(" ", "")
            if compact_variant:
                compact_variants.add(compact_variant)
            for token in variant.split(" "):
                token = token.strip()
                if token:
                    token_set.add(token)
        candidates.append(
            {
                "display": name,
                "variants": unique_variants,
                "tokens": token_set,
                "compact_variants": compact_variants,
            }
        )
    return candidates


def _build_parameter_lookup(param_display_names):
    lookup = {}
    for name in param_display_names or []:
        raw = (name or "").strip()
        if not raw:
            continue
        keys = [_normalize_name(raw), _alias_key(raw)]
        base = _base_parameter_name(raw)
        if base != raw:
            keys.extend([_normalize_name(base), _alias_key(base)])
        for variant in _match_text_variants(raw):
            keys.append(variant)
            keys.append(variant.replace(" ", ""))
        for key in keys:
            if key and key not in lookup:
                lookup[key] = name
    return lookup


def _standard_parameter_match(header, param_display_names):
    header_key = _alias_key(header)
    if not header_key:
        return None
    target_names = STANDARD_HEADER_PARAMETER_ALIASES.get(header_key)
    if not target_names:
        return None

    lookup = _build_parameter_lookup(param_display_names)
    for target_name in target_names:
        for key in (_normalize_name(target_name), _alias_key(target_name)):
            if key in lookup:
                return lookup[key]
        for variant in _match_text_variants(target_name):
            if variant in lookup:
                return lookup[variant]
            compact = variant.replace(" ", "")
            if compact in lookup:
                return lookup[compact]
    return None


def _contains_parameter_match(header, param_display_names):
    header_variants = _match_text_variants(header)
    if not header_variants:
        return None

    candidates = _build_parameter_candidates(param_display_names)
    if not candidates:
        return None

    ranked = []
    for candidate in candidates:
        best_rank = None
        best_length = None
        for header_variant in header_variants:
            header_compact = header_variant.replace(" ", "")
            if len(header_compact) < 3:
                continue

            header_tokens = [token for token in header_variant.split(" ") if token and len(token) >= 3]
            candidate_tokens = candidate.get("tokens", set())
            candidate_compacts = candidate.get("compact_variants", set())

            for token in header_tokens:
                if token in candidate_tokens:
                    length = min(abs(len(compact) - len(token)) for compact in candidate_compacts) if candidate_compacts else 0
                    rank = (0, length, len(candidate.get("display", "")), candidate.get("display", ""))
                    if best_rank is None or rank < best_rank:
                        best_rank = rank

            for compact in candidate_compacts:
                if compact == header_compact:
                    rank = (0, 0, len(candidate.get("display", "")), candidate.get("display", ""))
                elif compact.endswith(header_compact) or compact.startswith(header_compact):
                    rank = (1, abs(len(compact) - len(header_compact)), len(candidate.get("display", "")), candidate.get("display", ""))
                elif header_compact in compact:
                    rank = (2, abs(len(compact) - len(header_compact)), len(candidate.get("display", "")), candidate.get("display", ""))
                else:
                    continue
                if best_rank is None or rank < best_rank:
                    best_rank = rank
            if best_rank is not None:
                best_length = best_rank
        if best_length is not None:
            ranked.append((best_length, candidate.get("display")))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


def _score_parameter_match(header, candidate):
    header_variants = _match_text_variants(header)
    if not header_variants:
        return 0.0

    best_score = 0.0
    for header_variant in header_variants:
        header_compact = header_variant.replace(" ", "")
        header_tokens = set(token for token in header_variant.split(" ") if token)
        for candidate_variant in candidate.get("variants", []):
            candidate_compact = candidate_variant.replace(" ", "")
            candidate_tokens = set(token for token in candidate_variant.split(" ") if token)
            if not candidate_compact:
                continue
            if header_variant == candidate_variant or header_compact == candidate_compact:
                return 1.0

            # Strong auto-match when the header is a whole token in the parameter
            # name, e.g. GCA -> *_WWP_Stats_GCA.
            if len(header_tokens) == 1:
                header_token = next(iter(header_tokens))
                if len(header_token) >= 3 and header_token in candidate_tokens:
                    return 0.97

            # Strong auto-match when the normalized header text is clearly contained
            # in the parameter name.
            if len(header_compact) >= 3 and header_compact in candidate_compact:
                if candidate_compact.endswith(header_compact) or candidate_compact.startswith(header_compact):
                    best_score = max(best_score, 0.94)
                else:
                    best_score = max(best_score, 0.88)

            sequence_score = SequenceMatcher(None, header_compact, candidate_compact).ratio()
            overlap = 0.0
            if header_tokens and candidate_tokens:
                overlap = (2.0 * len(header_tokens & candidate_tokens)) / (len(header_tokens) + len(candidate_tokens))

            contains_bonus = 0.0
            shorter_len = min(len(header_compact), len(candidate_compact))
            if shorter_len >= 4 and (
                header_compact in candidate_compact or candidate_compact in header_compact
            ):
                contains_bonus = 0.08

            score = max(sequence_score, overlap) + contains_bonus
            if header_tokens and candidate_tokens and header_tokens <= candidate_tokens:
                score += 0.10
            if score > best_score:
                best_score = score
    return min(best_score, 1.0)


def _fuzzy_match_parameter(header, param_display_names):
    candidates = _build_parameter_candidates(param_display_names)
    if not candidates:
        return None

    scored = []
    for candidate in candidates:
        score = _score_parameter_match(header, candidate)
        if score > 0:
            scored.append((score, candidate["display"]))
    if not scored:
        return None

    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_name = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    if best_score >= 0.96:
        return best_name
    if best_score >= 0.90 and (best_score - second_score) >= 0.02:
        return best_name
    if best_score >= 0.82 and (best_score - second_score) >= 0.03:
        return best_name
    if best_score >= 0.72 and (best_score - second_score) >= 0.05:
        return best_name
    return None


def build_default_selections(headers, param_display_names):
    defaults = []
    name_lookup = _build_parameter_lookup(param_display_names)
    for header in headers:
        header_text = (header or "").strip()
        if not header_text:
            defaults.append(SKIP_OPTION)
            continue
        header_lower = _normalize_name(header_text)
        header_alias = _alias_key(header_text)
        if header_lower in ("key", "key name", "keyname") or header_alias in ("key", "key name", "keyname"):
            defaults.append(KEY_NAME_OPTION)
            continue
        standard_match = _standard_parameter_match(header_text, param_display_names)
        if standard_match:
            defaults.append(standard_match)
            continue
        exact_match = None
        for key in (_normalize_name(header_text), _alias_key(header_text)):
            if key in name_lookup:
                exact_match = name_lookup[key]
                break
        if exact_match:
            defaults.append(exact_match)
            continue
        fuzzy_match = _fuzzy_match_parameter(header_text, param_display_names)
        if fuzzy_match:
            defaults.append(fuzzy_match)
            continue
        defaults.append(SKIP_OPTION)
    return defaults


def create_key_schedule(doc, name, category_bic, category_label):
    cat_id = DB.ElementId(category_bic)
    schedule = None
    try:
        schedule = DB.ViewSchedule.CreateKeySchedule(doc, cat_id)
    except Exception:
        schedule = None
    if schedule is None:
        try:
            schedule = DB.ViewSchedule.CreateSchedule(doc, cat_id)
        except Exception:
            schedule = None
    if schedule is None:
        return None, "Failed to create {} key schedule.".format(category_label.lower())
    if not schedule.Definition or not schedule.Definition.IsKeySchedule:
        return None, "Created schedule is not a key schedule."
    schedule.Name = unique_view_name(doc, name)
    return schedule, ""


def add_schedule_fields(definition, column_mappings, param_map):
    added_param_ids = {}
    for mapping in column_mappings:
        if mapping.get("option") in (SKIP_OPTION, KEY_NAME_OPTION):
            continue
        param_id = param_map.get(mapping.get("option"))
        if not param_id:
            continue
        param_id_val = element_id_value(param_id)
        if param_id_val in added_param_ids:
            continue
        try:
            field = definition.AddField(DB.ScheduleFieldType.Instance, param_id)
            header = mapping.get("header") or ""
            if header:
                try:
                    field.ColumnHeading = header
                except Exception:
                    pass
            added_param_ids[param_id_val] = field
        except Exception:
            continue
    return added_param_ids


def get_key_schedules(doc, category_bic):
    schedules = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        try:
            if not view.Definition or not view.Definition.IsKeySchedule:
                continue
            cat_id = view.Definition.CategoryId
            if not _is_bic_value(cat_id, category_bic):
                continue
            schedules.append(view)
        except Exception:
            continue
    return schedules


def find_key_schedule_by_name(doc, category_bic, schedule_name):
    target = _normalize_name(schedule_name)
    if not target:
        return None
    for sched in get_key_schedules(doc, category_bic):
        try:
            if _normalize_name(sched.Name) == target:
                return sched
        except Exception:
            continue
    return None


def _normalize_name(value):
    if value is None:
        return ""
    return str(value).strip().lower()


def get_schedule_key_parameter_names(schedule):
    names = []
    try:
        key_name = (schedule.KeyScheduleParameterName or "").strip()
        if key_name:
            names.append(key_name)
    except Exception:
        pass
    if "Key Name" not in names:
        names.append("Key Name")
    return names


def get_schedule_key_name_value(elem, key_param_names):
    for param_name in key_param_names:
        param = elem.LookupParameter(param_name)
        if not param:
            continue
        try:
            value = param.AsString()
            if value:
                return value
        except Exception:
            pass
        try:
            value = param.AsValueString()
            if value:
                return value
        except Exception:
            pass
    return ""


def collect_schedule_key_elements(schedule):
    elements = []
    try:
        elements = list(DB.FilteredElementCollector(schedule.Document, schedule.Id).ToElements())
    except Exception:
        elements = []
    return [elem for elem in elements if elem is not None]


def build_schedule_key_element_map(schedule, key_param_names):
    key_map = {}
    duplicates = set()
    for elem in collect_schedule_key_elements(schedule):
        key_value = get_schedule_key_name_value(elem, key_param_names)
        key_norm = _normalize_name(key_value)
        if not key_norm:
            continue
        if key_norm in key_map:
            duplicates.add(key_norm)
            continue
        key_map[key_norm] = elem
    return key_map, duplicates


def build_key_usage_counts(doc, category_bic, host_key_param_name, key_ids):
    usage = dict((key_id, 0) for key_id in key_ids)
    if not host_key_param_name or not key_ids:
        return usage
    for elem in (
        DB.FilteredElementCollector(doc)
        .OfCategory(category_bic)
        .WhereElementIsNotElementType()
        .ToElements()
    ):
        param = elem.LookupParameter(host_key_param_name)
        if not param:
            continue
        try:
            if param.StorageType != DB.StorageType.ElementId:
                continue
        except Exception:
            continue
        try:
            ref_id = param.AsElementId()
        except Exception:
            continue
        ref_val = element_id_value(ref_id)
        if ref_val in usage:
            usage[ref_val] = usage.get(ref_val, 0) + 1
    return usage


def schedule_field_names_with_ids(schedule):
    names = {}
    try:
        definition = schedule.Definition
        field_order = list(definition.GetFieldOrder())
    except Exception:
        field_order = []
    for field_id in field_order:
        try:
            field = definition.GetField(field_id)
        except Exception:
            continue
        if not field:
            continue
        try:
            name = field.GetName()
        except Exception:
            name = ""
        try:
            heading = field.ColumnHeading
        except Exception:
            heading = ""
        for label in (name, heading):
            key = _normalize_name(label)
            if key and key not in names:
                names[key] = field_id
    return names


def build_field_index_map(definition):
    field_order = list(definition.GetFieldOrder()) if definition else []
    index_map = {}
    for idx, field_id in enumerate(field_order):
        try:
            field = definition.GetField(field_id)
        except Exception:
            continue
        if not field:
            continue
        try:
            param_id = field.ParameterId
        except Exception:
            param_id = None
        if param_id:
            index_map[element_id_value(param_id)] = idx
    return index_map


def format_cell_value(value):
    if value is None:
        return ""
    try:
        if hasattr(value, "isoformat"):
            return value.isoformat()
    except Exception:
        pass
    try:
        return str(value)
    except Exception:
        return ""


def _as_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except Exception:
            return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _as_yes_no_int(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        try:
            return 1 if int(value) != 0 else 0
        except Exception:
            return None
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "t", "1", "on", "checked", "x"):
        return 1
    if text in ("no", "n", "false", "f", "0", "off", "unchecked"):
        return 0
    return None


def _is_yes_no_parameter(param):
    try:
        definition = param.Definition
    except Exception:
        definition = None
    if not definition:
        return False
    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            spec = definition.GetDataType()
            bool_yesno = getattr(getattr(DB.SpecTypeId, "Boolean", None), "YesNo", None)
            if bool_yesno and spec == bool_yesno:
                return True
    except Exception:
        pass
    try:
        parameter_type = definition.ParameterType
        if hasattr(DB, "ParameterType") and parameter_type == DB.ParameterType.YesNo:
            return True
    except Exception:
        pass
    return False


def _as_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    try:
        return float(str(value).strip())
    except Exception:
        return None


def set_parameter_value(param, value):
    if param is None:
        return False
    storage = None
    try:
        storage = param.StorageType
    except Exception:
        storage = None
    text = format_cell_value(value)
    try:
        if storage == DB.StorageType.String:
            return param.Set(text)
        if storage == DB.StorageType.Integer:
            if _is_yes_no_parameter(param):
                # Yes/No parameters must be persisted as Revit booleans (0/1), never text.
                yn = _as_yes_no_int(value)
                if yn is None:
                    return False
                return param.Set(yn)
            num = _as_int(value)
            if num is None:
                return param.SetValueString(text)
            return param.Set(num)
        if storage == DB.StorageType.Double:
            num = _as_float(value)
            if num is None:
                return param.SetValueString(text)
            try:
                return param.Set(num)
            except Exception:
                return param.SetValueString(text)
    except Exception:
        return False
    return False


def create_key_elements(schedule, row_count):
    table = schedule.GetTableData()
    body = table.GetSectionData(DB.SectionType.Body)
    created_ids = []
    existing_ids = set(
        element_id_value(e.Id)
        for e in DB.FilteredElementCollector(schedule.Document, schedule.Id).ToElements()
    )
    for _ in range(row_count):
        try:
            body.InsertRow(body.LastRowNumber)
        except Exception:
            body.InsertRow(body.NumberOfRows)
        elements = DB.FilteredElementCollector(schedule.Document, schedule.Id).ToElements()
        new_elem = None
        for elem in elements:
            elem_id_val = element_id_value(elem.Id)
            if elem_id_val not in existing_ids:
                new_elem = elem
                break
        if new_elem is None and elements:
            new_elem = elements[-1]
        if new_elem is not None:
            created_ids.append(element_id_value(new_elem.Id))
            existing_ids.add(element_id_value(new_elem.Id))
    return created_ids


def create_single_key_element(schedule):
    created = create_key_elements(schedule, 1)
    if not created:
        return None
    elem_id = DB.ElementId(created[0])
    try:
        return schedule.Document.GetElement(elem_id)
    except Exception:
        return None


def main():
    doc = get_revit_doc()
    if doc is None:
        raise Exception("Active Revit document is not available.")
    ui = load_uiutils()
    config = _get_config()
    selected_target_type = _safe_config_get(config, "area_key_import_target", "") or TARGET_OPTIONS[0]
    target = resolve_schedule_target(selected_target_type)
    category_bic = target["bic"]
    category_label = target["label"]
    category_key = _normalize_name(category_label)

    param_names, param_map = get_category_parameter_options(doc, category_bic)
    parameter_options = [SKIP_OPTION, KEY_NAME_OPTION] + param_names
    saved_setting_sets = _load_saved_setting_sets(doc, config)

    def persist_saved_setting_sets(updated_sets):
        saved_setting_sets.clear()
        saved_setting_sets.update(updated_sets or {})
        _write_saved_setting_sets(doc, config, saved_setting_sets)

    last_file_path = _safe_config_get(config, "area_key_import_file_path", "") or ""

    state = {
        "file_path": last_file_path,
        "headers": [],
        "column_labels": [],
        "rows": [],
        "defaults": [],
        "auto_defaults": [],
        "schedule_name": "",
        "selected_target_type": selected_target_type,
    }

    # Preload the most recently used workbook for this target type.
    if state["file_path"] and os.path.exists(state["file_path"]):
        workbook = read_workbook(state["file_path"], ui)
        if workbook is not None:
            headers, column_labels, rows = extract_excel_data(workbook)
            if column_labels:
                signature = _header_signature(headers)
                auto_defaults = build_default_selections(headers, param_names)
                defaults = _load_saved_mapping(
                    config,
                    category_key,
                    signature,
                    len(column_labels),
                    parameter_options,
                    auto_defaults,
                )
                if defaults is None:
                    defaults = list(auto_defaults)
                state.update(
                    {
                        "headers": headers,
                        "column_labels": column_labels,
                        "rows": rows,
                        "defaults": defaults,
                        "auto_defaults": auto_defaults,
                    }
                )

    if not state["schedule_name"]:
        state["schedule_name"] = _safe_config_get(
            config,
            "area_key_import_schedule_name_{}".format(category_key),
            "",
        ) or _default_schedule_name(state["file_path"], category_label)

    while True:
        result = _show_area_keyplan_import_dialog(
            title=TITLE,
            file_path=state["file_path"],
            column_names=state["column_labels"],
            target_types=TARGET_OPTIONS,
            selected_target_type=state.get("selected_target_type", TARGET_OPTIONS[0]),
            parameter_options=parameter_options,
            default_selections=state["defaults"],
            auto_map_selections=state.get("auto_defaults", []),
            saved_setting_sets=saved_setting_sets,
            save_saved_setting_sets=persist_saved_setting_sets,
            width=980,
            height=780,
        )
        if result is None:
            return

        selected_target_type = result.get("selected_target_type") or state.get("selected_target_type", TARGET_OPTIONS[0])
        next_target = resolve_schedule_target(selected_target_type)
        next_category_bic = next_target["bic"]
        next_category_label = next_target["label"]
        next_category_key = _normalize_name(next_category_label)
        target_changed = next_category_bic != category_bic
        if target_changed:
            category_bic = next_category_bic
            category_label = next_category_label
            category_key = next_category_key
            state["selected_target_type"] = selected_target_type
            param_names, param_map = get_category_parameter_options(doc, category_bic)
            parameter_options = [SKIP_OPTION, KEY_NAME_OPTION] + param_names
            state["defaults"] = []
            state["auto_defaults"] = []
            state["schedule_name"] = _safe_config_get(
                config,
                "area_key_import_schedule_name_{}".format(category_key),
                "",
            ) or _default_schedule_name(state.get("file_path", ""), category_label)

        file_path = result.get("file_path") or ""
        state["file_path"] = file_path
        state["selected_target_type"] = selected_target_type

        current_selections = result.get("selected_options") or []
        if state["column_labels"] and len(current_selections) == len(state["column_labels"]):
            state["defaults"] = list(current_selections)
            _safe_config_set(config, "area_key_import_headers_signature", _header_signature(state.get("headers", [])))
            _safe_config_set(config, "area_key_import_selected_options", current_selections)
            _safe_config_set(config, _mapping_signature_key(category_key), _header_signature(state.get("headers", [])))
            _safe_config_set(config, _mapping_selection_key(category_key), current_selections)
        _safe_config_set(config, "area_key_import_target", selected_target_type)
        _safe_config_set(config, "area_key_import_file_path", file_path)
        _save_config()

        if result.get("load_requested"):
            if not file_path:
                ui.uiUtils_alert("Please select an Excel file.", title=TITLE)
                continue
            if not param_names:
                ui.uiUtils_alert(
                    "No {} instances found to read parameters from.".format(category_label),
                    title=TITLE,
                )
                continue
            workbook = read_workbook(file_path, ui)
            if workbook is None:
                continue
            headers, column_labels, rows = extract_excel_data(workbook)
            if not column_labels:
                ui.uiUtils_alert("No data found in the Excel file.", title=TITLE)
                continue
            signature = _header_signature(headers)
            auto_defaults = build_default_selections(headers, param_names)
            defaults = _load_saved_mapping(
                config,
                category_key,
                signature,
                len(column_labels),
                parameter_options,
                auto_defaults,
            )
            if defaults is None:
                defaults = list(auto_defaults)
            state.update(
                {
                    "headers": headers,
                    "column_labels": column_labels,
                    "rows": rows,
                    "defaults": defaults,
                    "auto_defaults": auto_defaults,
                }
            )
            saved_name = _safe_config_get(
                config,
                "area_key_import_schedule_name_{}".format(category_key),
                "",
            ) or ""
            state["schedule_name"] = saved_name or _default_schedule_name(state["file_path"], category_label)
            _safe_config_set(config, "area_key_import_headers_signature", signature)
            _safe_config_set(config, "area_key_import_selected_options", defaults)
            _safe_config_set(config, _mapping_signature_key(category_key), signature)
            _safe_config_set(config, _mapping_selection_key(category_key), defaults)
            _save_config()
            continue

        if target_changed:
            # Re-open once to refresh mapping options for the newly selected target type.
            continue

        if not state["column_labels"]:
            ui.uiUtils_alert("Load an Excel file first.", title=TITLE)
            continue

        selections = result.get("selected_options") or []
        if not selections or len(selections) != len(state["column_labels"]):
            ui.uiUtils_alert("Please review column mappings.", title=TITLE)
            continue

        column_mappings = []
        for idx, label in enumerate(state["column_labels"]):
            header = state["headers"][idx] if idx < len(state["headers"]) else ""
            option = selections[idx] if idx < len(selections) else SKIP_OPTION
            column_mappings.append(
                {
                    "index": idx,
                    "label": label,
                    "header": header,
                    "option": option,
                }
            )

        key_mapped = any(mapping["option"] == KEY_NAME_OPTION for mapping in column_mappings)
        if not key_mapped:
            ui.uiUtils_alert("Please map one column to 'Key Name'.", title=TITLE)
            continue

        schedule_name = (state.get("schedule_name") or "").strip()
        if not schedule_name:
            schedule_name = _default_schedule_name(state.get("file_path", ""), category_label)
        if not schedule_name:
            ui.uiUtils_alert("Unable to determine schedule name.", title=TITLE)
            continue

        key_column_indices = [
            m.get("index", 0)
            for m in column_mappings
            if m.get("option") == KEY_NAME_OPTION
        ]
        key_column_index = key_column_indices[0] if key_column_indices else None
        if key_column_index is None:
            ui.uiUtils_alert("Please map one column to 'Key Name'.", title=TITLE)
            continue

        created = 0
        updated = 0
        deleted = 0
        kept_in_use = 0
        skipped = 0
        errors = []
        warnings = []

        with RevitTransaction(doc, TITLE):
            schedule = find_key_schedule_by_name(doc, category_bic, schedule_name)
            if schedule is None:
                schedule, err = create_key_schedule(doc, schedule_name, category_bic, category_label)
                if schedule is None:
                    ui.uiUtils_alert(err or "Failed to create key schedule.", title=TITLE)
                    return

            definition = schedule.Definition
            add_schedule_fields(definition, column_mappings, param_map)

            key_param_names = get_schedule_key_parameter_names(schedule)
            key_param_name = key_param_names[0] if key_param_names else "Key Name"
            existing_key_map, duplicate_existing_keys = build_schedule_key_element_map(schedule, key_param_names)
            if duplicate_existing_keys:
                warnings.append(
                    "Existing schedule has duplicate key names. Only the first match was updated for: {}".format(
                        ", ".join(sorted(list(duplicate_existing_keys))[:10])
                    )
                )
            matched_existing_keys = set()
            seen_excel_keys = set()

            for idx, row in enumerate(state["rows"]):
                key_value = row[key_column_index] if key_column_index < len(row) else None
                key_text = format_cell_value(key_value).strip()
                key_norm = _normalize_name(key_text)
                if not key_norm:
                    errors.append("Row {}: key name is blank".format(idx + 1))
                    skipped += 1
                    continue
                if key_norm in seen_excel_keys:
                    errors.append("Row {}: duplicate key name '{}' in Excel".format(idx + 1, key_text))
                    skipped += 1
                    continue
                seen_excel_keys.add(key_norm)

                elem = existing_key_map.get(key_norm)
                is_new = False
                if elem is not None:
                    matched_existing_keys.add(key_norm)
                else:
                    elem = create_single_key_element(schedule)
                    is_new = True
                if elem is None:
                    errors.append("Row {} ({}): failed to create or find key row".format(idx + 1, key_text))
                    skipped += 1
                    continue

                for mapping in column_mappings:
                    option = mapping.get("option")
                    if option in (None, "", SKIP_OPTION):
                        continue
                    col_index = mapping.get("index", 0)
                    value = row[col_index] if col_index < len(row) else None

                    if option == KEY_NAME_OPTION:
                        param = None
                        for p_name in key_param_names:
                            param = elem.LookupParameter(p_name)
                            if param:
                                break
                        if not param:
                            errors.append("Row {} (Key Name): parameter not found".format(idx + 1))
                            continue
                        if not set_parameter_value(param, value):
                            errors.append("Row {} (Key Name): failed to set value".format(idx + 1))
                        continue

                    param = None
                    param_id = param_map.get(option)
                    if param_id:
                        try:
                            param = elem.get_Parameter(param_id)
                        except Exception:
                            param = None
                    if not param:
                        param_name = option
                        if param_name.endswith(")") and " (Id " in param_name:
                            param_name = param_name.split(" (Id ", 1)[0].strip()
                        param = elem.LookupParameter(param_name)
                    if not param:
                        errors.append("Row {} ({}): parameter not found".format(idx + 1, option))
                        continue
                    if not set_parameter_value(param, value):
                        errors.append("Row {} ({}): failed to set value".format(idx + 1, option))

                if is_new:
                    created += 1
                else:
                    updated += 1

            to_remove_keys = [
                key_norm
                for key_norm in existing_key_map.keys()
                if key_norm not in matched_existing_keys
            ]
            key_ids_to_check = [
                element_id_value(existing_key_map[k].Id)
                for k in to_remove_keys
                if existing_key_map.get(k) is not None
            ]
            usage_counts = build_key_usage_counts(doc, category_bic, key_param_name, key_ids_to_check)
            for key_norm in to_remove_keys:
                elem = existing_key_map.get(key_norm)
                if elem is None:
                    continue
                elem_id_val = element_id_value(elem.Id)
                if usage_counts.get(elem_id_val, 0) > 0:
                    kept_in_use += 1
                    continue
                try:
                    doc.Delete(elem.Id)
                    deleted += 1
                except Exception:
                    errors.append("Failed to delete old key '{}'".format(key_norm))

        # Persist latest choices for the next run.
        _safe_config_set(config, "area_key_import_target", state.get("selected_target_type", TARGET_OPTIONS[0]))
        _safe_config_set(config, "area_key_import_file_path", state.get("file_path", ""))
        _safe_config_set(config, "area_key_import_headers_signature", _header_signature(state.get("headers", [])))
        _safe_config_set(config, "area_key_import_selected_options", selections)
        _safe_config_set(config, _mapping_signature_key(category_key), _header_signature(state.get("headers", [])))
        _safe_config_set(config, _mapping_selection_key(category_key), selections)
        _safe_config_set(config, "area_key_import_schedule_name_{}".format(category_key), schedule_name)
        _save_config()

        summary = (
            "Rows created: {}\nRows updated: {}\nRows deleted: {}\n"
            "Rows kept (in use): {}\nRows skipped: {}"
        ).format(created, updated, deleted, kept_in_use, skipped)
        if warnings:
            summary += "\n\nWarnings:\n" + "\n".join(warnings[:10])
            if len(warnings) > 10:
                summary += "\n... ({} more)".format(len(warnings) - 10)
        if errors:
            summary += "\n\nErrors:\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                summary += "\n... ({} more)".format(len(errors) - 10)
        ui.uiUtils_alert(summary, title=TITLE)
        return


if __name__ == "__main__":
    main()
