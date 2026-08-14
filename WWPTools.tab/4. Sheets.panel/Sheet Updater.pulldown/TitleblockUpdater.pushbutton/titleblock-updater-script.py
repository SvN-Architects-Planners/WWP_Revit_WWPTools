import clr
import math
import os
import sys
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB, UI

from pyrevit import script
from WWP_settings import get_tool_settings
from WWP_uiUtils import uiUtils_alert
from WWP_versioning import apply_window_title

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

output = script.get_output()
legacy_sources = []
try:
    legacy_sources.append(script.get_config())
except Exception:
    pass
config, save_config = get_tool_settings(
    "TitleblockUpdater",
    doc=doc,
    legacy_sources=legacy_sources,
)

def _print_text(text=""):
    try:
        print(text)
        if hasattr(sys.stdout, "flush"):
            sys.stdout.flush()
    except Exception:
        pass

def _elem_id_int(element_id):
    if element_id is None:
        return None
    try:
        return int(element_id.Value)  # Revit 2024+
    except Exception:
        pass
    try:
        return int(element_id.IntegerValue)  # Revit 2023-
    except Exception:
        pass
    try:
        return int(element_id)
    except Exception:
        return None

def _is_yes_no_parameter(param):
    if not param or not getattr(param, "Definition", None):
        return False
    definition = param.Definition
    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            data_type = definition.GetDataType()
            yes_no_type = getattr(getattr(DB.SpecTypeId, "Boolean", None), "YesNo", None)
            if yes_no_type is not None and data_type == yes_no_type:
                return True
    except Exception:
        pass
    try:
        if hasattr(definition, "ParameterType") and definition.ParameterType == DB.ParameterType.YesNo:
            return True
    except Exception:
        pass
    return False

def _is_angle_parameter(param):
    """True if the parameter's data type is Angle or Rotation Angle (modern
    spec, falling back to the legacy ParameterType when neither modern check
    matches). Rotation Angle is a distinct data type from plain Angle in
    Revit's family editor -- north arrow / key plan rotation parameters are
    conventionally authored as Rotation Angle, not Angle."""
    if not param or not getattr(param, "Definition", None):
        return False
    definition = param.Definition
    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            data_type = definition.GetDataType()
            if data_type == DB.SpecTypeId.Angle:
                return True
            rotation_angle_type = getattr(getattr(DB.SpecTypeId, "Rotation", None), "Angle", None)
            if rotation_angle_type is not None and data_type == rotation_angle_type:
                return True
    except Exception:
        pass
    try:
        if hasattr(definition, "ParameterType"):
            if definition.ParameterType == DB.ParameterType.Angle:
                return True
            rotation_angle_param_type = getattr(DB.ParameterType, "RotationAngle", None)
            if rotation_angle_param_type is not None and definition.ParameterType == rotation_angle_param_type:
                return True
    except Exception:
        pass
    return False

def _is_supported_scale_parameter(param):
    try:
        return (
            param.StorageType in (DB.StorageType.Double, DB.StorageType.Integer)
            and not _is_yes_no_parameter(param)
        )
    except Exception:
        return False

def _collect_titleblocks():
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )

def _get_param_entries(titleblock_instances, matches_param):
    """Shared family-grouped parameter scan. `matches_param(param)` decides inclusion."""
    family_params = {}
    entries = {}
    for tb in titleblock_instances:
        if not tb:
            continue
        try:
            family_name = tb.Symbol.Family.Name
        except Exception:
            family_name = "Unknown"
        if family_name not in family_params:
            family_params[family_name] = {}
        try:
            for p in tb.Parameters:
                if not p or not p.Definition:
                    continue
                if not matches_param(p):
                    continue
                name = p.Definition.Name
                if name:
                    key = "{} [Instance]".format(name)
                    family_params[family_name][key] = {"name": name, "scope": "instance"}
                    entries[key] = {"name": name, "scope": "instance"}
        except Exception:
            pass
        try:
            symbol = tb.Symbol if hasattr(tb, "Symbol") else None
            if symbol:
                for p in symbol.Parameters:
                    if not p or not p.Definition:
                        continue
                    if not matches_param(p):
                        continue
                    name = p.Definition.Name
                    if name:
                        key = "{} [Type]".format(name)
                        family_params[family_name][key] = {"name": name, "scope": "type"}
                        entries[key] = {"name": name, "scope": "type"}
        except Exception:
            pass
    groups = [
        (fname, sorted(fparams.keys()))
        for fname, fparams in sorted(family_params.items())
        if fparams
    ]
    return groups, entries

def _get_param_entries_angle(titleblock_instances):
    return _get_param_entries(titleblock_instances, _is_angle_parameter)

def _get_param_entries_scale(titleblock_instances):
    return _get_param_entries(
        titleblock_instances,
        lambda p: _is_supported_scale_parameter(p) and "scale" in (p.Definition.Name or "").lower(),
    )

def _get_yesno_param_entries(titleblock_instances):
    """Return sorted list of writable Yes/No instance parameter names."""
    names = set()
    for tb in titleblock_instances:
        if not tb:
            continue
        try:
            for p in tb.Parameters:
                if not p or not p.Definition:
                    continue
                if p.IsReadOnly:
                    continue
                if not _is_yes_no_parameter(p):
                    continue
                name = p.Definition.Name
                if name:
                    names.add(name)
        except Exception:
            continue
    return sorted(names)

def _lookup_parameter(element, param_name):
    if not element or not param_name:
        return None
    try:
        return element.LookupParameter(param_name)
    except Exception:
        return None

def _resolve_target_parameter(sheet, titleblock_instance, target_param_name, target_param_scope):
    if target_param_scope == "type":
        symbol = titleblock_instance.Symbol if titleblock_instance and hasattr(titleblock_instance, "Symbol") else None
        param = _lookup_parameter(symbol, target_param_name)
        return param, "titleblock type" if param else None

    candidates = []
    titleblock_param = _lookup_parameter(titleblock_instance, target_param_name)
    if titleblock_param:
        candidates.append(("titleblock instance", titleblock_param))
    sheet_param = _lookup_parameter(sheet, target_param_name)
    if sheet_param:
        candidates.append(("sheet", sheet_param))

    for owner_name, param in candidates:
        try:
            if not param.IsReadOnly:
                return param, owner_name
        except Exception:
            continue

    if candidates:
        return candidates[0][1], candidates[0][0]
    return None, None

def _get_north_angle_for_view(view, north_vector):
    """Angle (deg, CCW from view up) from north_vector to the view's up direction."""
    up = view.UpDirection
    right = view.RightDirection
    north_up = north_vector.DotProduct(up)
    north_right = north_vector.DotProduct(right)
    # Negate north_right to get CCW angle from view up (matches Revit's convention)
    return round(math.degrees(math.atan2(-north_right, north_up)) % 360.0, 4)

def _get_true_north_angle_for_view(doc, view):
    """
    Returns the True North angle (degrees) as seen in the given view.
    Angle is measured counter-clockwise from the view's up direction to True North.
    """
    try:
        proj_pos = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        angle_rad = proj_pos.Angle  # CCW from Project North (+Y) to True North
    except Exception:
        angle_rad = 0.0

    tn = DB.XYZ(-math.sin(angle_rad), math.cos(angle_rad), 0)

    try:
        return _get_north_angle_for_view(view, tn)
    except Exception:
        return round(math.degrees(angle_rad) % 360.0, 4)

def _get_project_north_angle_for_view(view):
    """
    Returns the Project North angle (degrees) as seen in the given view.
    Project North is always the model's +Y axis in internal coordinates.
    """
    try:
        return _get_north_angle_for_view(view, DB.XYZ(0, 1, 0))
    except Exception:
        return 0.0

def _populate_parameter_combo(combo, param_groups, default_label):
    from System.Windows import FontWeights, Thickness
    from System.Windows.Controls import ComboBoxItem, Separator

    for i, (family_name, keys) in enumerate(param_groups or []):
        if not keys:
            continue
        if i > 0:
            combo.Items.Add(Separator())
        header = ComboBoxItem()
        header.Content = family_name
        header.IsEnabled = False
        header.FontWeight = FontWeights.Bold
        combo.Items.Add(header)
        for key in keys:
            ci = ComboBoxItem()
            ci.Content = key
            ci.Tag = key
            ci.Padding = Thickness(24, 3, 8, 3)
            combo.Items.Add(ci)

    default_set = False
    if default_label:
        for item in combo.Items:
            if hasattr(item, 'Tag') and str(item.Tag or '') == default_label:
                combo.SelectedItem = item
                default_set = True
                break
    if not default_set:
        for item in combo.Items:
            if hasattr(item, 'Tag') and item.Tag is not None:
                combo.SelectedItem = item
                break

def _selected_combo_value(combo):
    _sel = combo.SelectedItem
    if _sel is not None and hasattr(_sel, 'Tag') and _sel.Tag is not None:
        return str(_sel.Tag)
    return str(combo.Text or "").strip()

def _update_arrow_for_sheet(
    titleblock_instance,
    sheet,
    sheet_label,
    primary_view,
    target_param_name,
    target_param_scope,
    set_visibility,
    visibility_param_name,
    hide_on_elev_section,
    angle_value,
    arrow_tag,
):
    """
    Sets target_param_name to angle_value on the resolved parameter, optionally
    toggling visibility_param_name. Returns (status, message) where status is
    "updated" | "hidden" | "failed" and message is None when status == "updated".
    """
    _HIDE_VIEW_TYPES = (DB.ViewType.Elevation, DB.ViewType.Section)

    if primary_view is None:
        if hide_on_elev_section and set_visibility and visibility_param_name:
            try:
                vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                    vis_p.Set(0)
                return "hidden", sheet_label + " - No plan view found (north arrow hidden){}".format(arrow_tag)
            except Exception:
                pass
        return "failed", sheet_label + " - No suitable viewport found{}".format(arrow_tag)

    if hide_on_elev_section and set_visibility and visibility_param_name and primary_view.ViewType in _HIDE_VIEW_TYPES:
        try:
            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                vis_p.Set(0)
        except Exception:
            pass
        return "hidden", sheet_label + " - Primary view is {}, north arrow hidden{}".format(
            "elevation" if primary_view.ViewType == DB.ViewType.Elevation else "section", arrow_tag
        )

    param, resolved_owner = _resolve_target_parameter(sheet, titleblock_instance, target_param_name, target_param_scope)
    if not param:
        return "failed", sheet_label + " - Target parameter missing: {}{}".format(target_param_name, arrow_tag)

    try:
        if param.IsReadOnly:
            return "failed", sheet_label + " - Parameter is read-only ({}){}".format(resolved_owner or "", arrow_tag)
        elif param.StorageType == DB.StorageType.Double:
            value_to_set = math.radians(angle_value) if _is_angle_parameter(param) else float(angle_value)
            param.Set(value_to_set)
            if set_visibility and visibility_param_name:
                vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                    vis_p.Set(1)
        elif param.StorageType == DB.StorageType.Integer:
            param.Set(int(round(angle_value)))
            if set_visibility and visibility_param_name:
                vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                    vis_p.Set(1)
        else:
            return "failed", sheet_label + " - Unsupported storage type: {}{}".format(param.StorageType, arrow_tag)
    except Exception as e:
        return "failed", sheet_label + " - Failed to set parameter: {}{}".format(str(e), arrow_tag)

    return "updated", None

def _build_scale_tab_state(window, sheet_items, param_groups, yesno_labels,
                            prechecked_indices, default_label, default_visibility_label):
    prefix = "SC_"
    from System.Windows import Visibility
    from System.Windows.Controls import ListBoxItem

    enable_checkbox = window.FindName(prefix + "EnableCheckBox")
    parameter_combo = window.FindName(prefix + "ParameterCombo")
    search_box = window.FindName(prefix + "SearchBox")
    sheets_list = window.FindName(prefix + "SheetsList")
    all_sheets_checkbox = window.FindName(prefix + "AllSheetsCheckBox")
    all_sheets_warning = window.FindName(prefix + "AllSheetsWarningText")
    validation_text = window.FindName(prefix + "ValidationText")
    set_visibility_checkbox = window.FindName(prefix + "SetVisibilityCheckBox")
    visibility_param_combo = window.FindName(prefix + "VisibilityParamCombo")
    hide_checkbox = window.FindName(prefix + "HideCheckBox")
    extra_checkbox = window.FindName(prefix + "IgnoreDraftingViewsCheckBox")

    selected_indices = set(prechecked_indices or [])
    param_labels = [key for _, keys in (param_groups or []) for key in keys]

    _populate_parameter_combo(parameter_combo, param_groups, default_label)

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = True
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False

    for label in (yesno_labels or []):
        visibility_param_combo.Items.Add(label)
    if default_visibility_label and default_visibility_label in (yesno_labels or []):
        visibility_param_combo.SelectedItem = default_visibility_label
    elif visibility_param_combo.Items.Count > 0:
        visibility_param_combo.SelectedIndex = 0
    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = False
    if hide_checkbox is not None:
        hide_checkbox.IsChecked = False
        hide_checkbox.IsEnabled = False

    def _on_visibility_checked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = True
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = True

    def _on_visibility_unchecked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = False
        if hide_checkbox is not None:
            hide_checkbox.IsChecked = False
            hide_checkbox.IsEnabled = False

    if set_visibility_checkbox is not None:
        set_visibility_checkbox.Checked += _on_visibility_checked
        set_visibility_checkbox.Unchecked += _on_visibility_unchecked

    def _set_validation(message):
        if validation_text is None:
            return
        if message:
            validation_text.Text = message
            validation_text.Visibility = Visibility.Visible
        else:
            validation_text.Text = ""
            validation_text.Visibility = Visibility.Collapsed

    def _visible_items():
        term = (search_box.Text or "").strip().lower()
        if not term:
            return list(enumerate(sheet_items))
        return [(i, label) for i, label in enumerate(sheet_items) if term in label.lower()]

    def _render_sheets():
        sheets_list.Items.Clear()
        for index, label in _visible_items():
            item = ListBoxItem()
            item.Content = label
            item.Tag = index
            sheets_list.Items.Add(item)
        for item in sheets_list.Items:
            try:
                if int(item.Tag) in selected_indices:
                    sheets_list.SelectedItems.Add(item)
            except Exception:
                pass

    def _sync_selected():
        visible_indices = []
        for item in sheets_list.Items:
            try:
                visible_indices.append(int(item.Tag))
            except Exception:
                pass
        for index in visible_indices:
            if index in selected_indices:
                selected_indices.remove(index)
        for item in sheets_list.SelectedItems:
            try:
                selected_indices.add(int(item.Tag))
            except Exception:
                pass

    def _on_all_sheets_checked(sender, args):
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Visible
        _set_validation("")

    def _on_all_sheets_unchecked(sender, args):
        search_box.IsEnabled = True
        sheets_list.IsEnabled = True
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Collapsed

    def _on_search_changed(sender, args):
        _sync_selected()
        _render_sheets()

    def _on_selection_changed(sender, args):
        _sync_selected()
        if selected_indices:
            _set_validation("")

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.Checked += _on_all_sheets_checked
        all_sheets_checkbox.Unchecked += _on_all_sheets_unchecked
    search_box.TextChanged += _on_search_changed
    sheets_list.SelectionChanged += _on_selection_changed

    def _apply_enabled_state(is_enabled):
        for ctrl in (parameter_combo, search_box, sheets_list, all_sheets_checkbox, set_visibility_checkbox, extra_checkbox):
            if ctrl is not None:
                ctrl.IsEnabled = is_enabled
        if is_enabled:
            visibility_on = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
            if visibility_param_combo is not None:
                visibility_param_combo.IsEnabled = visibility_on
            if hide_checkbox is not None:
                hide_checkbox.IsEnabled = visibility_on
        else:
            if visibility_param_combo is not None:
                visibility_param_combo.IsEnabled = False
            if hide_checkbox is not None:
                hide_checkbox.IsEnabled = False
        _set_validation("")

    def _on_enable_changed(sender, args):
        _apply_enabled_state(bool(enable_checkbox is None or enable_checkbox.IsChecked))

    if enable_checkbox is not None:
        enable_checkbox.Checked += _on_enable_changed
        enable_checkbox.Unchecked += _on_enable_changed

    _render_sheets()

    def is_enabled():
        return bool(enable_checkbox is None or enable_checkbox.IsChecked)

    def validate():
        if not is_enabled():
            _set_validation("")
            return True
        selected_param = _selected_combo_value(parameter_combo)
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return False
        if not selected_param:
            _set_validation("Select a parameter.")
            return False
        if selected_param not in (param_labels or []):
            _set_validation("Select a valid parameter from the dropdown.")
            return False
        _set_validation("")
        return True

    def get_result():
        selected_param = _selected_combo_value(parameter_combo)
        return {
            "enabled": is_enabled(),
            "selected_indices": sorted(selected_indices),
            "selected_parameter": selected_param,
            "set_visibility": bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked),
            "visibility_param": str(visibility_param_combo.Text or "").strip() if (set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked) else "",
            "hide_on": bool(hide_checkbox is not None and hide_checkbox.IsChecked),
            "ignore_drafting_views": bool(extra_checkbox is not None and extra_checkbox.IsChecked),
        }

    return {"validate": validate, "get_result": get_result, "is_enabled": is_enabled}

def _build_true_north_tab_state(window, sheet_items, param_groups, yesno_labels,
                                 prechecked_indices, default_label, default_visibility_label,
                                 project_north_enabled_default, default_project_north_label,
                                 default_project_north_visibility_label):
    prefix = "TN_"
    from System.Windows import Visibility
    from System.Windows.Controls import ListBoxItem

    enable_checkbox = window.FindName(prefix + "EnableCheckBox")
    parameter_combo = window.FindName(prefix + "ParameterCombo")
    search_box = window.FindName(prefix + "SearchBox")
    sheets_list = window.FindName(prefix + "SheetsList")
    all_sheets_checkbox = window.FindName(prefix + "AllSheetsCheckBox")
    all_sheets_warning = window.FindName(prefix + "AllSheetsWarningText")
    validation_text = window.FindName(prefix + "ValidationText")
    set_visibility_checkbox = window.FindName(prefix + "SetVisibilityCheckBox")
    visibility_param_combo = window.FindName(prefix + "VisibilityParamCombo")
    hide_checkbox = window.FindName(prefix + "HideCheckBox")
    enable_pn_checkbox = window.FindName(prefix + "EnableProjectNorthCheckBox")
    pn_parameter_combo = window.FindName(prefix + "ProjectNorthParameterCombo")
    set_pn_visibility_checkbox = window.FindName(prefix + "SetProjectNorthVisibilityCheckBox")
    pn_visibility_param_combo = window.FindName(prefix + "ProjectNorthVisibilityParamCombo")

    selected_indices = set(prechecked_indices or [])
    param_labels = [key for _, keys in (param_groups or []) for key in keys]

    _populate_parameter_combo(parameter_combo, param_groups, default_label)
    _populate_parameter_combo(pn_parameter_combo, param_groups, default_project_north_label)

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = True
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False

    for label in (yesno_labels or []):
        visibility_param_combo.Items.Add(label)
        pn_visibility_param_combo.Items.Add(label)
    if default_visibility_label and default_visibility_label in (yesno_labels or []):
        visibility_param_combo.SelectedItem = default_visibility_label
    elif visibility_param_combo.Items.Count > 0:
        visibility_param_combo.SelectedIndex = 0
    if default_project_north_visibility_label and default_project_north_visibility_label in (yesno_labels or []):
        pn_visibility_param_combo.SelectedItem = default_project_north_visibility_label
    elif pn_visibility_param_combo.Items.Count > 0:
        pn_visibility_param_combo.SelectedIndex = 0

    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = False
    if set_pn_visibility_checkbox is not None:
        set_pn_visibility_checkbox.IsChecked = False
        set_pn_visibility_checkbox.IsEnabled = False
    if enable_pn_checkbox is not None:
        enable_pn_checkbox.IsChecked = bool(project_north_enabled_default)
        if pn_parameter_combo is not None:
            pn_parameter_combo.IsEnabled = bool(project_north_enabled_default)
        if set_pn_visibility_checkbox is not None:
            set_pn_visibility_checkbox.IsEnabled = bool(project_north_enabled_default)
    if hide_checkbox is not None:
        hide_checkbox.IsChecked = False
        hide_checkbox.IsEnabled = False

    def _update_hide_availability():
        any_visibility_enabled = bool(
            (set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked) or
            (set_pn_visibility_checkbox is not None and set_pn_visibility_checkbox.IsChecked)
        )
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = any_visibility_enabled
            if not any_visibility_enabled:
                hide_checkbox.IsChecked = False

    def _on_visibility_checked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = True
        _update_hide_availability()

    def _on_visibility_unchecked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = False
        _update_hide_availability()

    def _on_pn_checked(sender, args):
        if pn_parameter_combo is not None:
            pn_parameter_combo.IsEnabled = True
        if set_pn_visibility_checkbox is not None:
            set_pn_visibility_checkbox.IsEnabled = True

    def _on_pn_unchecked(sender, args):
        if pn_parameter_combo is not None:
            pn_parameter_combo.IsEnabled = False
        if set_pn_visibility_checkbox is not None:
            set_pn_visibility_checkbox.IsChecked = False
            set_pn_visibility_checkbox.IsEnabled = False
        _update_hide_availability()

    def _on_pn_visibility_checked(sender, args):
        if pn_visibility_param_combo is not None:
            pn_visibility_param_combo.IsEnabled = True
        _update_hide_availability()

    def _on_pn_visibility_unchecked(sender, args):
        if pn_visibility_param_combo is not None:
            pn_visibility_param_combo.IsEnabled = False
        _update_hide_availability()

    if set_visibility_checkbox is not None:
        set_visibility_checkbox.Checked += _on_visibility_checked
        set_visibility_checkbox.Unchecked += _on_visibility_unchecked
    if enable_pn_checkbox is not None:
        enable_pn_checkbox.Checked += _on_pn_checked
        enable_pn_checkbox.Unchecked += _on_pn_unchecked
    if set_pn_visibility_checkbox is not None:
        set_pn_visibility_checkbox.Checked += _on_pn_visibility_checked
        set_pn_visibility_checkbox.Unchecked += _on_pn_visibility_unchecked

    def _set_validation(message):
        if validation_text is None:
            return
        if message:
            validation_text.Text = message
            validation_text.Visibility = Visibility.Visible
        else:
            validation_text.Text = ""
            validation_text.Visibility = Visibility.Collapsed

    def _visible_items():
        term = (search_box.Text or "").strip().lower()
        if not term:
            return list(enumerate(sheet_items))
        return [(i, label) for i, label in enumerate(sheet_items) if term in label.lower()]

    def _render_sheets():
        sheets_list.Items.Clear()
        for index, label in _visible_items():
            item = ListBoxItem()
            item.Content = label
            item.Tag = index
            sheets_list.Items.Add(item)
        for item in sheets_list.Items:
            try:
                if int(item.Tag) in selected_indices:
                    sheets_list.SelectedItems.Add(item)
            except Exception:
                pass

    def _sync_selected():
        visible_indices = []
        for item in sheets_list.Items:
            try:
                visible_indices.append(int(item.Tag))
            except Exception:
                pass
        for index in visible_indices:
            if index in selected_indices:
                selected_indices.remove(index)
        for item in sheets_list.SelectedItems:
            try:
                selected_indices.add(int(item.Tag))
            except Exception:
                pass

    def _on_all_sheets_checked(sender, args):
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Visible
        _set_validation("")

    def _on_all_sheets_unchecked(sender, args):
        search_box.IsEnabled = True
        sheets_list.IsEnabled = True
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Collapsed

    def _on_search_changed(sender, args):
        _sync_selected()
        _render_sheets()

    def _on_selection_changed(sender, args):
        _sync_selected()
        if selected_indices:
            _set_validation("")

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.Checked += _on_all_sheets_checked
        all_sheets_checkbox.Unchecked += _on_all_sheets_unchecked
    search_box.TextChanged += _on_search_changed
    sheets_list.SelectionChanged += _on_selection_changed

    def _apply_enabled_state(is_enabled):
        for ctrl in (parameter_combo, search_box, sheets_list, all_sheets_checkbox,
                     set_visibility_checkbox, enable_pn_checkbox):
            if ctrl is not None:
                ctrl.IsEnabled = is_enabled
        if is_enabled:
            visibility_on = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
            if visibility_param_combo is not None:
                visibility_param_combo.IsEnabled = visibility_on
            pn_on = bool(enable_pn_checkbox is not None and enable_pn_checkbox.IsChecked)
            if pn_parameter_combo is not None:
                pn_parameter_combo.IsEnabled = pn_on
            if set_pn_visibility_checkbox is not None:
                set_pn_visibility_checkbox.IsEnabled = pn_on
            pn_visibility_on = bool(pn_on and set_pn_visibility_checkbox is not None and set_pn_visibility_checkbox.IsChecked)
            if pn_visibility_param_combo is not None:
                pn_visibility_param_combo.IsEnabled = pn_visibility_on
            _update_hide_availability()
        else:
            if visibility_param_combo is not None:
                visibility_param_combo.IsEnabled = False
            if pn_parameter_combo is not None:
                pn_parameter_combo.IsEnabled = False
            if set_pn_visibility_checkbox is not None:
                set_pn_visibility_checkbox.IsEnabled = False
            if pn_visibility_param_combo is not None:
                pn_visibility_param_combo.IsEnabled = False
            if hide_checkbox is not None:
                hide_checkbox.IsEnabled = False
        _set_validation("")

    def _on_enable_changed(sender, args):
        _apply_enabled_state(bool(enable_checkbox is None or enable_checkbox.IsChecked))

    if enable_checkbox is not None:
        enable_checkbox.Checked += _on_enable_changed
        enable_checkbox.Unchecked += _on_enable_changed

    _render_sheets()

    def is_enabled():
        return bool(enable_checkbox is None or enable_checkbox.IsChecked)

    def validate():
        if not is_enabled():
            _set_validation("")
            return True
        selected_param = _selected_combo_value(parameter_combo)
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return False
        if not selected_param:
            _set_validation("Select a True North parameter.")
            return False
        if selected_param not in (param_labels or []):
            _set_validation("Select a valid True North parameter from the dropdown.")
            return False
        if enable_pn_checkbox is not None and enable_pn_checkbox.IsChecked:
            pn_param = _selected_combo_value(pn_parameter_combo)
            if not pn_param or pn_param not in (param_labels or []):
                _set_validation("Select a valid Project North parameter from the dropdown.")
                return False
            if pn_param == selected_param:
                _set_validation("Project North must use a different parameter than True North.")
                return False
        _set_validation("")
        return True

    def get_result():
        selected_param = _selected_combo_value(parameter_combo)
        pn_enabled = bool(enable_pn_checkbox is not None and enable_pn_checkbox.IsChecked)
        pn_param = _selected_combo_value(pn_parameter_combo) if pn_enabled else ""
        pn_set_visibility = bool(pn_enabled and set_pn_visibility_checkbox is not None and set_pn_visibility_checkbox.IsChecked)
        return {
            "enabled": is_enabled(),
            "selected_indices": sorted(selected_indices),
            "selected_parameter": selected_param,
            "set_visibility": bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked),
            "visibility_param": str(visibility_param_combo.Text or "").strip() if (set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked) else "",
            "hide_on": bool(hide_checkbox is not None and hide_checkbox.IsChecked),
            "project_north_enabled": pn_enabled,
            "project_north_param": pn_param,
            "set_project_north_visibility": pn_set_visibility,
            "project_north_visibility_param": str(pn_visibility_param_combo.Text or "").strip() if pn_set_visibility else "",
        }

    return {"validate": validate, "get_result": get_result, "is_enabled": is_enabled}

def _show_titleblock_dialog(
    sheet_items,
    tn_param_groups,
    sc_param_groups,
    yesno_labels,
    tn_prechecked_indices,
    sc_prechecked_indices,
    tn_default_label,
    sc_default_label,
    tn_default_visibility_label,
    sc_default_visibility_label,
    tn_project_north_enabled_default,
    tn_default_project_north_label,
    tn_default_project_north_visibility_label,
):
    if not sheet_items:
        return None

    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    clr.AddReference("System.Xml")

    from System.IO import File, StringReader
    from System import Uri
    from System.Windows import Visibility
    from System.Windows.Interop import WindowInteropHelper
    from System.Windows.Markup import XamlReader
    from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
    from System.Xml import XmlReader

    xaml_path = os.path.join(os.path.dirname(__file__), "TitleblockUpdaterDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    xaml_reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(xaml_reader)
    apply_window_title(window, "Titleblock Updater")

    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass

    prompt_text = window.FindName("PromptText")
    tab_control = window.FindName("MainTabControl")
    global_validation_text = window.FindName("GlobalValidationText")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")

    prompt_text.Text = "Select what to update and choose the target parameter(s):"

    tn_state = _build_true_north_tab_state(
        window, sheet_items, tn_param_groups, yesno_labels,
        tn_prechecked_indices, tn_default_label, tn_default_visibility_label,
        tn_project_north_enabled_default, tn_default_project_north_label,
        tn_default_project_north_visibility_label,
    )
    sc_state = _build_scale_tab_state(
        window, sheet_items, sc_param_groups, yesno_labels,
        sc_prechecked_indices, sc_default_label, sc_default_visibility_label,
    )

    try:
        lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))
        logo_path = os.path.join(lib_path, "WWPtools-logo.png")
        if logo_image is not None and os.path.isfile(logo_path):
            bitmap = BitmapImage()
            bitmap.BeginInit()
            bitmap.UriSource = Uri(logo_path)
            bitmap.CacheOption = BitmapCacheOption.OnLoad
            bitmap.EndInit()
            logo_image.Source = bitmap
    except Exception:
        pass

    def _set_global_validation(message):
        if message:
            global_validation_text.Text = message
            global_validation_text.Visibility = Visibility.Visible
        else:
            global_validation_text.Text = ""
            global_validation_text.Visibility = Visibility.Collapsed

    def _on_ok(sender, args):
        _set_global_validation("")
        if not tn_state["is_enabled"]() and not sc_state["is_enabled"]():
            _set_global_validation("Enable at least one update (True North or Scale).")
            return
        tn_ok = tn_state["validate"]()
        if not tn_ok:
            tab_control.SelectedIndex = 0
            return
        sc_ok = sc_state["validate"]()
        if not sc_ok:
            tab_control.SelectedIndex = 1
            return
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    ok_button.Click += _on_ok
    cancel_button.Click += _on_cancel

    if window.ShowDialog() != True:
        return None

    return {
        "true_north": tn_state["get_result"](),
        "scale": sc_state["get_result"](),
    }

def main():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheets.sort(key=lambda s: (s.SheetNumber or "", s.Name or ""))
    if not sheets:
        UI.TaskDialog.Show("Titleblock Updater", "No sheets found.")
        return

    active_view = doc.ActiveView
    current_sheet_id_val = None
    if active_view and hasattr(active_view, "ViewType") and active_view.ViewType == DB.ViewType.DrawingSheet:
        current_sheet_id_val = _elem_id_int(active_view.Id)

    if current_sheet_id_val is not None:
        current_first, remaining = [], []
        for sheet in sheets:
            if _elem_id_int(sheet.Id) == current_sheet_id_val:
                current_first.append(sheet)
            else:
                remaining.append(sheet)
        sheets = current_first + remaining

    sheet_items = []
    sheet_by_index = []
    for sheet in sheets:
        number = sheet.SheetNumber or ""
        name = sheet.Name or ""
        label = "{} - {}".format(number, name)
        if current_sheet_id_val is not None and _elem_id_int(sheet.Id) == current_sheet_id_val:
            label = "[Current Sheet] " + label
        sheet_items.append(label)
        sheet_by_index.append(sheet)

    def _prechecked_for(last_sheet_ids):
        indices = []
        if last_sheet_ids:
            for i, s in enumerate(sheet_by_index):
                sheet_id_val = _elem_id_int(s.Id)
                if sheet_id_val is not None and sheet_id_val in last_sheet_ids:
                    indices.append(i)
        elif current_sheet_id_val is not None:
            for i, s in enumerate(sheet_by_index):
                if _elem_id_int(s.Id) == current_sheet_id_val:
                    indices.append(i)
                    break
        return indices

    tn_last_sheet_ids = getattr(config, "tn_sheet_ids", []) or []
    tn_last_param_name = getattr(config, "tn_param_name", "") or ""
    tn_last_param_scope = getattr(config, "tn_param_scope", "") or ""
    tn_last_visibility_param = getattr(config, "tn_visibility_param_name", "") or ""
    tn_last_pn_enabled = bool(getattr(config, "tn_project_north_enabled", False))
    tn_last_pn_param_name = getattr(config, "tn_project_north_param_name", "") or ""
    tn_last_pn_param_scope = getattr(config, "tn_project_north_param_scope", "") or ""
    tn_last_pn_visibility_param = getattr(config, "tn_project_north_visibility_param_name", "") or ""

    sc_last_sheet_ids = getattr(config, "sc_sheet_ids", []) or []
    sc_last_param_name = getattr(config, "sc_param_name", "") or ""
    sc_last_param_scope = getattr(config, "sc_param_scope", "") or ""
    sc_last_visibility_param = getattr(config, "sc_visibility_param_name", "") or ""

    titleblocks = _collect_titleblocks()
    if not titleblocks:
        UI.TaskDialog.Show("Titleblock Updater", "No titleblock found on any sheet.")
        return

    tn_param_groups, tn_param_entries = _get_param_entries_angle(titleblocks)
    sc_param_groups, sc_param_entries = _get_param_entries_scale(titleblocks)
    if not tn_param_groups and not sc_param_groups:
        UI.TaskDialog.Show(
            "Titleblock Updater",
            "No angle or scale parameters found on titleblock instances.",
        )
        return

    yesno_labels = _get_yesno_param_entries(titleblocks)

    tn_default_label = None
    if tn_last_param_name and tn_last_param_scope:
        tn_default_label = "{} [{}]".format(tn_last_param_name, "Instance" if tn_last_param_scope == "instance" else "Type")
    sc_default_label = None
    if sc_last_param_name and sc_last_param_scope:
        sc_default_label = "{} [{}]".format(sc_last_param_name, "Instance" if sc_last_param_scope == "instance" else "Type")

    tn_default_pn_label = None
    if tn_last_pn_param_name and tn_last_pn_param_scope:
        tn_default_pn_label = "{} [{}]".format(tn_last_pn_param_name, "Instance" if tn_last_pn_param_scope == "instance" else "Type")

    try:
        dialog_result = _show_titleblock_dialog(
            sheet_items,
            tn_param_groups,
            sc_param_groups,
            yesno_labels,
            _prechecked_for(tn_last_sheet_ids),
            _prechecked_for(sc_last_sheet_ids),
            tn_default_label,
            sc_default_label,
            tn_last_visibility_param if tn_last_visibility_param in yesno_labels else None,
            sc_last_visibility_param if sc_last_visibility_param in yesno_labels else None,
            tn_last_pn_enabled,
            tn_default_pn_label,
            tn_last_pn_visibility_param if tn_last_pn_visibility_param in yesno_labels else None,
        )
    except Exception as ex:
        UI.TaskDialog.Show("Titleblock Updater", "WPF UI error:\n{}".format(str(ex)))
        return

    if not dialog_result:
        uiUtils_alert("Operation cancelled.", "Titleblock Updater")
        return

    tn = dialog_result.get("true_north") or {}
    sc = dialog_result.get("scale") or {}

    if not tn.get("enabled") and not sc.get("enabled"):
        uiUtils_alert("Nothing selected. Operation cancelled.", "Titleblock Updater")
        return

    titleblocks_by_sheet = {}
    for tb in titleblocks:
        try:
            titleblocks_by_sheet[tb.OwnerViewId] = tb
        except Exception:
            continue

    tn_updated_sheets, tn_failed_sheets, tn_hidden_sheets = [], [], []
    sc_updated_sheets, sc_failed_sheets, sc_warning_sheets = [], [], []
    tn_param_name = tn_param_scope = None
    sc_param_name = sc_param_scope = None
    pn_param_name = pn_param_scope = None
    enable_project_north = False

    t = DB.Transaction(doc, "Update Titleblock Parameters")
    t.Start()
    try:
        if tn.get("enabled"):
            entry = tn_param_entries.get(tn.get("selected_parameter"))
            tn_param_name = entry["name"] if entry else tn.get("selected_parameter")
            tn_param_scope = entry["scope"] if entry else "instance"
            set_visibility = bool(tn.get("set_visibility"))
            visibility_param_name = tn.get("visibility_param") or ""
            hide_on_elev_section = bool(tn.get("hide_on"))

            enable_project_north = bool(tn.get("project_north_enabled"))
            pn_entry = tn_param_entries.get(tn.get("project_north_param")) if enable_project_north else None
            pn_param_name = pn_entry["name"] if pn_entry else tn.get("project_north_param")
            pn_param_scope = pn_entry["scope"] if pn_entry else "instance"
            set_pn_visibility = bool(tn.get("set_project_north_visibility"))
            pn_visibility_param_name = tn.get("project_north_visibility_param") or ""

            view_cache = {}
            for i in tn.get("selected_indices", []):
                sheet = sheet_by_index[i]
                sheet_number = sheet.SheetNumber or ""
                sheet_name = sheet.Name or ""
                sheet_label = "{} - {}".format(sheet_number, sheet_name) if sheet_number else sheet_name

                titleblock_instance = titleblocks_by_sheet.get(sheet.Id)
                if not titleblock_instance:
                    tn_failed_sheets.append(sheet_label + " - No titleblock found")
                    continue

                primary_view = None
                for vp_id in sheet.GetAllViewports():
                    viewport = doc.GetElement(vp_id)
                    if not viewport:
                        continue
                    view_id = viewport.ViewId
                    if view_id in view_cache:
                        view = view_cache[view_id]
                    else:
                        view = doc.GetElement(view_id)
                        view_cache[view_id] = view
                    if not view or not hasattr(view, "ViewType"):
                        continue
                    if view.ViewType in (DB.ViewType.Legend, DB.ViewType.DraftingView, DB.ViewType.Schedule):
                        continue
                    primary_view = view
                    break

                true_north_angle = _get_true_north_angle_for_view(doc, primary_view) if primary_view else 0.0
                status, message = _update_arrow_for_sheet(
                    titleblock_instance, sheet, sheet_label, primary_view,
                    tn_param_name, tn_param_scope,
                    set_visibility, visibility_param_name,
                    hide_on_elev_section, true_north_angle,
                    arrow_tag="",
                )
                if status == "updated":
                    tn_updated_sheets.append(sheet_name)
                elif status == "hidden":
                    tn_hidden_sheets.append(message)
                else:
                    tn_failed_sheets.append(message)

                if enable_project_north:
                    project_north_angle = _get_project_north_angle_for_view(primary_view) if primary_view else 0.0
                    pn_status, pn_message = _update_arrow_for_sheet(
                        titleblock_instance, sheet, sheet_label, primary_view,
                        pn_param_name, pn_param_scope,
                        set_pn_visibility, pn_visibility_param_name,
                        hide_on_elev_section, project_north_angle,
                        arrow_tag=" (Project North)",
                    )
                    if pn_status == "updated":
                        tn_updated_sheets.append(sheet_name + " (Project North)")
                    elif pn_status == "hidden":
                        tn_hidden_sheets.append(pn_message)
                    else:
                        tn_failed_sheets.append(pn_message)

        if sc.get("enabled"):
            entry = sc_param_entries.get(sc.get("selected_parameter"))
            sc_param_name = entry["name"] if entry else sc.get("selected_parameter")
            sc_param_scope = entry["scope"] if entry else "instance"
            ignore_drafting_views = bool(sc.get("ignore_drafting_views"))
            set_visibility = bool(sc.get("set_visibility"))
            visibility_param_name = sc.get("visibility_param") or ""
            hide_on_no_scale = bool(sc.get("hide_on"))

            view_scale_cache = {}
            for i in sc.get("selected_indices", []):
                sheet = sheet_by_index[i]
                sheet_number = sheet.SheetNumber or ""
                sheet_name = sheet.Name
                sheet_label = "{} - {}".format(sheet_number, sheet_name) if sheet_number else sheet_name

                titleblock_instance = titleblocks_by_sheet.get(sheet.Id)
                if not titleblock_instance:
                    sc_failed_sheets.append(sheet_label + " - No titleblock found")
                    continue

                viewport_ids = sheet.GetAllViewports()
                if viewport_ids.Count == 0:
                    sc_failed_sheets.append(sheet_label + " - No viewports")
                    continue

                scales = set()
                legend_views_skipped = 0
                drafting_views_skipped = 0
                non_legend_view_count = 0
                non_legend_drafting_count = 0
                for vp_id in viewport_ids:
                    viewport = doc.GetElement(vp_id)
                    if not viewport:
                        continue
                    view_id = viewport.ViewId
                    if view_id in view_scale_cache:
                        sval = view_scale_cache[view_id]
                        is_legend = view_scale_cache.get(("legend", view_id), False)
                        is_drafting = view_scale_cache.get(("drafting", view_id), False)
                    else:
                        view = doc.GetElement(view_id)
                        is_legend = bool(view and hasattr(view, "ViewType") and view.ViewType == DB.ViewType.Legend)
                        is_drafting = bool(view and hasattr(view, "ViewType") and view.ViewType == DB.ViewType.DraftingView)
                        sval = view.Scale if view and hasattr(view, 'Scale') else None
                        view_scale_cache[view_id] = sval
                        view_scale_cache[("legend", view_id)] = is_legend
                        view_scale_cache[("drafting", view_id)] = is_drafting
                    if is_legend:
                        legend_views_skipped += 1
                        continue
                    non_legend_view_count += 1
                    if is_drafting:
                        non_legend_drafting_count += 1
                    if ignore_drafting_views and is_drafting:
                        drafting_views_skipped += 1
                        continue
                    if sval is not None and sval > 0:
                        scales.add(sval)

                only_drafting = bool(non_legend_view_count and non_legend_view_count == non_legend_drafting_count)
                if only_drafting:
                    warning_message = sheet_label + " - Only drafting views found on sheet"
                    if ignore_drafting_views:
                        warning_message += " (ignored for scale calculation)"
                    sc_warning_sheets.append(warning_message)

                if not scales:
                    if hide_on_no_scale and set_visibility and visibility_param_name:
                        try:
                            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                                vis_p.Set(0)
                        except Exception:
                            pass
                    if only_drafting and ignore_drafting_views:
                        sc_failed_sheets.append(sheet_label + " - Only drafting views found and ignored")
                    else:
                        sc_failed_sheets.append(sheet_label + " - No valid scales found")
                    continue

                sheet_scale_value = list(scales)[0] if len(scales) == 1 else 0

                sheet_scale_param, resolved_target_owner = _resolve_target_parameter(
                    sheet, titleblock_instance, sc_param_name, sc_param_scope
                )

                if not sheet_scale_param:
                    sc_failed_sheets.append(sheet_label + " - Target parameter missing: {}".format(sc_param_name))
                    continue

                try:
                    if sheet_scale_param.IsReadOnly:
                        owner_label = resolved_target_owner or "resolved target"
                        sc_failed_sheets.append(sheet_label + " - Parameter is read-only ({})".format(owner_label))
                    elif sheet_scale_param.StorageType == DB.StorageType.Double:
                        sheet_scale_param.Set(float(sheet_scale_value))
                        if set_visibility and visibility_param_name:
                            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                                vis_p.Set(1)
                        sc_updated_sheets.append(sheet_name)
                    elif sheet_scale_param.StorageType == DB.StorageType.Integer:
                        sheet_scale_param.Set(int(sheet_scale_value))
                        if set_visibility and visibility_param_name:
                            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                                vis_p.Set(1)
                        sc_updated_sheets.append(sheet_name)
                    elif sheet_scale_param.StorageType == DB.StorageType.String:
                        sheet_scale_param.Set(str(sheet_scale_value))
                        if set_visibility and visibility_param_name:
                            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                                vis_p.Set(1)
                        sc_updated_sheets.append(sheet_name)
                    else:
                        sheet_scale_param.Set(sheet_scale_value)
                        if set_visibility and visibility_param_name:
                            vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                            if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                                vis_p.Set(1)
                        sc_updated_sheets.append(sheet_name)
                except Exception as e:
                    sc_failed_sheets.append(sheet_label + " - Failed to set parameter: {}".format(str(e)))

        t.Commit()
    except Exception as e:
        t.RollBack()
        UI.TaskDialog.Show("Error", str(e))
        return

    # Persist settings after a successful commit.
    config.tn_enabled = bool(tn.get("enabled"))
    if tn.get("enabled"):
        config.tn_sheet_ids = [v for v in (_elem_id_int(sheet_by_index[i].Id) for i in tn.get("selected_indices", [])) if v is not None]
        config.tn_param_name = tn_param_name
        config.tn_param_scope = tn_param_scope
        config.tn_visibility_param_name = tn.get("visibility_param") if tn.get("set_visibility") else ""
        config.tn_hide_on_elev_section = bool(tn.get("hide_on"))
        config.tn_project_north_enabled = enable_project_north
        config.tn_project_north_param_name = pn_param_name if enable_project_north else ""
        config.tn_project_north_param_scope = pn_param_scope if enable_project_north else ""
        config.tn_project_north_visibility_param_name = tn.get("project_north_visibility_param") if (enable_project_north and tn.get("set_project_north_visibility")) else ""

    config.sc_enabled = bool(sc.get("enabled"))
    if sc.get("enabled"):
        config.sc_sheet_ids = [v for v in (_elem_id_int(sheet_by_index[i].Id) for i in sc.get("selected_indices", [])) if v is not None]
        config.sc_param_name = sc_param_name
        config.sc_param_scope = sc_param_scope
        config.sc_visibility_param_name = sc.get("visibility_param") if sc.get("set_visibility") else ""
        config.sc_hide_on_no_scale = bool(sc.get("hide_on"))
        config.sc_ignore_drafting_views = bool(sc.get("ignore_drafting_views"))
    save_config()

    msg_lines = []
    if tn.get("enabled"):
        line = "True North: updated {} sheet(s).".format(len(tn_updated_sheets))
        if tn_hidden_sheets:
            line += " Hidden: {}.".format(len(tn_hidden_sheets))
        if tn_failed_sheets:
            line += " Failed/Skipped: {}.".format(len(tn_failed_sheets))
        msg_lines.append(line)
    if sc.get("enabled"):
        line = "Scale: updated {} sheet(s).".format(len(sc_updated_sheets))
        if sc_warning_sheets:
            line += " Warnings: {}.".format(len(sc_warning_sheets))
        if sc_failed_sheets:
            line += " Failed/Skipped: {}.".format(len(sc_failed_sheets))
        msg_lines.append(line)
    UI.TaskDialog.Show("Titleblock Updater", "\n".join(msg_lines))

    _print_text("")
    _print_text("Titleblock Updater Report")
    _print_text("Developed by: Jason Tian")
    if tn.get("enabled"):
        _print_text("")
        _print_text("-- True North --")
        _print_text("Target Parameter (True North): {} ({})".format(tn_param_name, tn_param_scope or "instance"))
        if enable_project_north:
            _print_text("Target Parameter (Project North): {} ({})".format(pn_param_name, pn_param_scope or "instance"))
        _print_text("Updated: {} sheets".format(len(tn_updated_sheets)))
        _print_text("Hidden: {} sheets".format(len(tn_hidden_sheets)))
        _print_text("Failed/Skipped: {} sheets".format(len(tn_failed_sheets)))
        for name in tn_updated_sheets:
            _print_text(" - {}".format(name))
        for h in tn_hidden_sheets:
            _print_text(" - {}".format(h))
        for f in tn_failed_sheets:
            _print_text(" - {}".format(f))
    if sc.get("enabled"):
        _print_text("")
        _print_text("-- Scale --")
        _print_text("Target Parameter: {} ({})".format(sc_param_name, sc_param_scope or "instance"))
        _print_text("Ignore Drafting Views: {}".format("Yes" if sc.get("ignore_drafting_views") else "No"))
        _print_text("Updated: {} sheets".format(len(sc_updated_sheets)))
        _print_text("Warnings: {} sheets".format(len(sc_warning_sheets)))
        _print_text("Failed/Skipped: {} sheets".format(len(sc_failed_sheets)))
        for name in sc_updated_sheets:
            _print_text(" - {}".format(name))
        for w in sc_warning_sheets:
            _print_text(" - {}".format(w))
        for f in sc_failed_sheets:
            _print_text(" - {}".format(f))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        uiUtils_alert(traceback.format_exc(), "Titleblock Updater")
