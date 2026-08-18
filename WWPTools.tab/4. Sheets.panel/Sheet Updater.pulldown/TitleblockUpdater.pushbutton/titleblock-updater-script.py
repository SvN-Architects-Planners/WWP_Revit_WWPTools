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
            rotation_angle_type = getattr(DB.SpecTypeId, "RotationAngle", None)
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
    """Return family-grouped and flat writable Yes/No instance parameters."""
    family_params = {}
    names = set()
    for tb in titleblock_instances:
        if not tb:
            continue
        try:
            family_name = tb.Symbol.Family.Name
        except Exception:
            family_name = "Unknown"
        if family_name not in family_params:
            family_params[family_name] = set()
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
                    family_params[family_name].add(name)
        except Exception:
            continue
    groups = [
        (family_name, sorted(param_names))
        for family_name, param_names in sorted(family_params.items())
        if param_names
    ]
    return groups, sorted(names)

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

def _populate_parameter_list(list_box, param_groups, default_labels):
    from System.Windows import Thickness
    from System.Windows.Controls import ListBoxItem, TextBlock
    from System.Windows.Documents import Run
    from System.Windows.Media import Brushes

    defaults = set(default_labels or [])
    applied_defaults = set()
    first_selectable = None
    for family_name, keys in (param_groups or []):
        if not keys:
            continue
        for key in keys:
            item = ListBoxItem()
            label = TextBlock()
            family_run = Run("{}: ".format(family_name))
            family_run.Foreground = Brushes.Gray
            parameter_run = Run(key)
            parameter_run.Foreground = Brushes.Black
            label.Inlines.Add(family_run)
            label.Inlines.Add(parameter_run)
            item.Content = label
            item.Tag = key
            item.Padding = Thickness(8, 6, 8, 6)
            item.ToolTip = "{}: {}".format(family_name, key)
            list_box.Items.Add(item)
            if first_selectable is None:
                first_selectable = item
            if key in defaults and key not in applied_defaults:
                list_box.SelectedItems.Add(item)
                applied_defaults.add(key)
    if list_box.SelectedItems.Count == 0 and first_selectable is not None:
        list_box.SelectedItems.Add(first_selectable)

def _selected_list_values(list_box):
    values = []
    seen = set()
    if list_box is None:
        return values
    for item in list_box.SelectedItems:
        if hasattr(item, "Tag") and item.Tag is not None:
            value = str(item.Tag)
        elif hasattr(item, "Content"):
            value = str(item.Content)
        else:
            value = str(item)
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values

def _set_visibility_parameters(titleblock_instance, parameter_names, value):
    for parameter_name in (parameter_names or []):
        try:
            vis_param = titleblock_instance.LookupParameter(parameter_name)
            if vis_param and not vis_param.IsReadOnly and vis_param.StorageType == DB.StorageType.Integer:
                vis_param.Set(int(value))
        except Exception:
            pass

def _update_scale_targets_for_sheet(sheet, titleblock_instance, targets, scale_value):
    """Update all selected scale targets. Returns an error string or None."""
    for target in (targets or []):
        target_name = target.get("name") or ""
        target_scope = target.get("scope") or "instance"
        scale_param, resolved_owner = _resolve_target_parameter(
            sheet, titleblock_instance, target_name, target_scope
        )
        if not scale_param:
            return "Target parameter missing: {}".format(target_name)
        try:
            if scale_param.IsReadOnly:
                return "Parameter is read-only: {} ({})".format(
                    target_name, resolved_owner or "resolved target"
                )
            if scale_param.StorageType == DB.StorageType.Double:
                scale_param.Set(float(scale_value))
            elif scale_param.StorageType == DB.StorageType.Integer:
                scale_param.Set(int(scale_value))
            elif scale_param.StorageType == DB.StorageType.String:
                scale_param.Set(str(scale_value))
            else:
                scale_param.Set(scale_value)
        except Exception as ex:
            return "Failed to set {}: {}".format(target_name, str(ex))
    return None

def _update_arrow_for_sheet(
    titleblock_instance,
    sheet,
    sheet_label,
    primary_view,
    target_parameters,
    set_visibility,
    visibility_param_names,
    hide_on_unsupported_view,
    angle_value,
    arrow_tag,
):
    """
    Sets every selected target to angle_value and optionally toggles every
    selected visibility parameter. Returns (status, message) where status is
    "updated" | "hidden" | "failed" and message is None when status == "updated".
    """
    hide_view_types = (DB.ViewType.Elevation, DB.ViewType.Section, DB.ViewType.ThreeD)

    if primary_view is None:
        if hide_on_unsupported_view and set_visibility and visibility_param_names:
            _set_visibility_parameters(titleblock_instance, visibility_param_names, 0)
            return "hidden", sheet_label + " - No plan view found (north arrow hidden){}".format(arrow_tag)
        return "failed", sheet_label + " - No suitable viewport found{}".format(arrow_tag)

    if hide_on_unsupported_view and set_visibility and visibility_param_names and primary_view.ViewType in hide_view_types:
        _set_visibility_parameters(titleblock_instance, visibility_param_names, 0)
        view_type_label = {
            DB.ViewType.Elevation: "elevation",
            DB.ViewType.Section: "section",
            DB.ViewType.ThreeD: "3D",
        }.get(primary_view.ViewType, str(primary_view.ViewType))
        return "hidden", sheet_label + " - Primary view is {}, north arrow hidden{}".format(
            view_type_label, arrow_tag
        )

    for target in (target_parameters or []):
        target_name = target.get("name") or ""
        target_scope = target.get("scope") or "instance"
        param, resolved_owner = _resolve_target_parameter(
            sheet, titleblock_instance, target_name, target_scope
        )
        if not param:
            return "failed", sheet_label + " - Target parameter missing: {}{}".format(target_name, arrow_tag)
        try:
            if param.IsReadOnly:
                return "failed", sheet_label + " - Parameter is read-only: {} ({}){}".format(
                    target_name, resolved_owner or "resolved target", arrow_tag
                )
            if param.StorageType == DB.StorageType.Double:
                value_to_set = math.radians(angle_value) if _is_angle_parameter(param) else float(angle_value)
                param.Set(value_to_set)
            elif param.StorageType == DB.StorageType.Integer:
                param.Set(int(round(angle_value)))
            else:
                return "failed", sheet_label + " - Unsupported storage type for {}: {}{}".format(
                    target_name, param.StorageType, arrow_tag
                )
        except Exception as e:
            return "failed", sheet_label + " - Failed to set {}: {}{}".format(target_name, str(e), arrow_tag)

    if set_visibility and visibility_param_names:
        _set_visibility_parameters(titleblock_instance, visibility_param_names, 1)

    return "updated", None

def _build_scale_tab_state(window, sheet_items, param_groups, yesno_param_groups, yesno_labels,
                            prechecked_indices, default_labels, default_visibility_labels,
                            visibility_enabled_default, hide_default,
                            ignore_drafting_default, all_sheets_default):
    prefix = "SC_"
    from System.Windows import Visibility
    from System.Windows.Controls import ListBoxItem

    parameter_list = window.FindName(prefix + "ParameterList")
    search_box = window.FindName(prefix + "SearchBox")
    sheets_list = window.FindName(prefix + "SheetsList")
    all_sheets_checkbox = window.FindName(prefix + "AllSheetsCheckBox")
    all_sheets_warning = window.FindName(prefix + "AllSheetsWarningText")
    validation_text = window.FindName(prefix + "ValidationText")
    set_visibility_checkbox = window.FindName(prefix + "SetVisibilityCheckBox")
    visibility_param_list = window.FindName(prefix + "VisibilityParamList")
    hide_checkbox = window.FindName(prefix + "HideCheckBox")
    extra_checkbox = window.FindName(prefix + "IgnoreDraftingViewsCheckBox")

    selected_indices = set(prechecked_indices or [])
    param_labels = [key for _, keys in (param_groups or []) for key in keys]

    _populate_parameter_list(parameter_list, param_groups, default_labels)
    _populate_parameter_list(visibility_param_list, yesno_param_groups, default_visibility_labels)

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = bool(all_sheets_default)
        search_box.IsEnabled = not bool(all_sheets_default)
        sheets_list.IsEnabled = not bool(all_sheets_default)
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Visible if all_sheets_default else Visibility.Collapsed
    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = bool(visibility_enabled_default)
    if hide_checkbox is not None:
        hide_checkbox.IsChecked = bool(hide_default)
        hide_checkbox.IsEnabled = bool(visibility_enabled_default)
    if extra_checkbox is not None:
        extra_checkbox.IsChecked = bool(ignore_drafting_default)

    def _on_visibility_checked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = True
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = True

    def _on_visibility_unchecked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = False
        if hide_checkbox is not None:
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

    def _apply_control_state():
        for ctrl in (parameter_list, all_sheets_checkbox, set_visibility_checkbox, extra_checkbox):
            if ctrl is not None:
                ctrl.IsEnabled = True
        use_all = bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked)
        for ctrl in (search_box, sheets_list):
            if ctrl is not None:
                ctrl.IsEnabled = not use_all
        visibility_on = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = visibility_on
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = visibility_on
        _set_validation("")

    _render_sheets()
    _apply_control_state()

    def validate():
        selected_params = _selected_list_values(parameter_list)
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return False
        if not selected_params:
            _set_validation("Select at least one target parameter.")
            return False
        if any(value not in (param_labels or []) for value in selected_params):
            _set_validation("Select valid target parameters from the list.")
            return False
        if set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked and not _selected_list_values(visibility_param_list):
            _set_validation("Select at least one visibility parameter.")
            return False
        _set_validation("")
        return True

    def get_result():
        selected_params = _selected_list_values(parameter_list)
        visibility_enabled = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        return {
            "enabled": True,
            "selected_indices": sorted(selected_indices),
            "selected_parameters": selected_params,
            "set_visibility": visibility_enabled,
            "visibility_params": _selected_list_values(visibility_param_list) if visibility_enabled else [],
            "hide_on": bool(hide_checkbox is not None and hide_checkbox.IsChecked),
            "ignore_drafting_views": bool(extra_checkbox is not None and extra_checkbox.IsChecked),
            "all_sheets": bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked),
        }

    return {"validate": validate, "get_result": get_result}

def _build_true_north_tab_state(window, sheet_items, param_groups, yesno_param_groups, yesno_labels,
                                 prechecked_indices, default_labels, default_visibility_labels,
                                 visibility_enabled_default, hide_default,
                                 all_sheets_default):
    prefix = "TN_"
    from System.Windows import Visibility
    from System.Windows.Controls import ListBoxItem

    parameter_list = window.FindName(prefix + "ParameterList")
    search_box = window.FindName(prefix + "SearchBox")
    sheets_list = window.FindName(prefix + "SheetsList")
    all_sheets_checkbox = window.FindName(prefix + "AllSheetsCheckBox")
    all_sheets_warning = window.FindName(prefix + "AllSheetsWarningText")
    validation_text = window.FindName(prefix + "ValidationText")
    set_visibility_checkbox = window.FindName(prefix + "SetVisibilityCheckBox")
    visibility_param_list = window.FindName(prefix + "VisibilityParamList")
    hide_checkbox = window.FindName(prefix + "HideCheckBox")

    selected_indices = set(prechecked_indices or [])
    param_labels = [key for _, keys in (param_groups or []) for key in keys]

    _populate_parameter_list(parameter_list, param_groups, default_labels)
    _populate_parameter_list(visibility_param_list, yesno_param_groups, default_visibility_labels)

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = bool(all_sheets_default)
        search_box.IsEnabled = not bool(all_sheets_default)
        sheets_list.IsEnabled = not bool(all_sheets_default)
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Visible if all_sheets_default else Visibility.Collapsed
    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = bool(visibility_enabled_default)
    if hide_checkbox is not None:
        hide_checkbox.IsChecked = bool(hide_default)
        hide_checkbox.IsEnabled = bool(visibility_enabled_default)

    def _on_visibility_checked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = True
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = True

    def _on_visibility_unchecked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = False
        if hide_checkbox is not None:
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

    def _apply_control_state():
        for ctrl in (parameter_list, all_sheets_checkbox, set_visibility_checkbox):
            if ctrl is not None:
                ctrl.IsEnabled = True
        use_all = bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked)
        for ctrl in (search_box, sheets_list):
            if ctrl is not None:
                ctrl.IsEnabled = not use_all
        visibility_on = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = visibility_on
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = visibility_on
        _set_validation("")

    _render_sheets()
    _apply_control_state()

    def validate():
        selected_params = _selected_list_values(parameter_list)
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return False
        if not selected_params:
            _set_validation("Select at least one True North parameter.")
            return False
        if any(value not in (param_labels or []) for value in selected_params):
            _set_validation("Select valid True North parameters from the list.")
            return False
        if set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked and not _selected_list_values(visibility_param_list):
            _set_validation("Select at least one True North visibility parameter.")
            return False
        _set_validation("")
        return True

    def get_result():
        selected_params = _selected_list_values(parameter_list)
        visibility_enabled = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        return {
            "enabled": True,
            "selected_indices": sorted(selected_indices),
            "selected_parameters": selected_params,
            "set_visibility": visibility_enabled,
            "visibility_params": _selected_list_values(visibility_param_list) if visibility_enabled else [],
            "hide_on": bool(hide_checkbox is not None and hide_checkbox.IsChecked),
            "all_sheets": bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked),
        }

    return {"validate": validate, "get_result": get_result}

def _build_project_north_tab_state(window, sheet_items, param_groups, yesno_param_groups, yesno_labels,
                                    prechecked_indices, default_labels, default_visibility_labels,
                                    visibility_enabled_default, hide_default,
                                    all_sheets_default):
    prefix = "PN_"
    from System.Windows import Visibility
    from System.Windows.Controls import ListBoxItem

    parameter_list = window.FindName(prefix + "ParameterList")
    search_box = window.FindName(prefix + "SearchBox")
    sheets_list = window.FindName(prefix + "SheetsList")
    all_sheets_checkbox = window.FindName(prefix + "AllSheetsCheckBox")
    all_sheets_warning = window.FindName(prefix + "AllSheetsWarningText")
    validation_text = window.FindName(prefix + "ValidationText")
    set_visibility_checkbox = window.FindName(prefix + "SetVisibilityCheckBox")
    visibility_param_list = window.FindName(prefix + "VisibilityParamList")
    hide_checkbox = window.FindName(prefix + "HideCheckBox")

    selected_indices = set(prechecked_indices or [])
    param_labels = [key for _, keys in (param_groups or []) for key in keys]

    _populate_parameter_list(parameter_list, param_groups, default_labels)
    _populate_parameter_list(visibility_param_list, yesno_param_groups, default_visibility_labels)

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = bool(all_sheets_default)
        search_box.IsEnabled = not bool(all_sheets_default)
        sheets_list.IsEnabled = not bool(all_sheets_default)
        if all_sheets_warning is not None:
            all_sheets_warning.Visibility = Visibility.Visible if all_sheets_default else Visibility.Collapsed
    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = bool(visibility_enabled_default)
    if hide_checkbox is not None:
        hide_checkbox.IsChecked = bool(hide_default)
        hide_checkbox.IsEnabled = bool(visibility_enabled_default)

    def _on_visibility_checked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = True
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = True

    def _on_visibility_unchecked(sender, args):
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = False
        if hide_checkbox is not None:
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

    def _apply_control_state():
        for ctrl in (parameter_list, all_sheets_checkbox, set_visibility_checkbox):
            if ctrl is not None:
                ctrl.IsEnabled = True
        use_all = bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked)
        for ctrl in (search_box, sheets_list):
            if ctrl is not None:
                ctrl.IsEnabled = not use_all
        visibility_on = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        if visibility_param_list is not None:
            visibility_param_list.IsEnabled = visibility_on
        if hide_checkbox is not None:
            hide_checkbox.IsEnabled = visibility_on
        _set_validation("")

    _render_sheets()
    _apply_control_state()

    def validate():
        selected_params = _selected_list_values(parameter_list)
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return False
        if not selected_params:
            _set_validation("Select at least one Project North(Construction North) parameter.")
            return False
        if any(value not in (param_labels or []) for value in selected_params):
            _set_validation("Select valid Project North(Construction North) parameters from the list.")
            return False
        if set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked and not _selected_list_values(visibility_param_list):
            _set_validation("Select at least one Project North visibility parameter.")
            return False
        _set_validation("")
        return True

    def get_result():
        selected_params = _selected_list_values(parameter_list)
        visibility_enabled = bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked)
        return {
            "enabled": True,
            "selected_indices": sorted(selected_indices),
            "selected_parameters": selected_params,
            "set_visibility": visibility_enabled,
            "visibility_params": _selected_list_values(visibility_param_list) if visibility_enabled else [],
            "hide_on": bool(hide_checkbox is not None and hide_checkbox.IsChecked),
            "all_sheets": bool(all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked),
        }

    return {"validate": validate, "get_result": get_result}

def _show_titleblock_dialog(
    sheet_items,
    tn_param_groups,
    pn_param_groups,
    sc_param_groups,
    yesno_param_groups,
    yesno_labels,
    tn_prechecked_indices,
    pn_prechecked_indices,
    sc_prechecked_indices,
    tn_default_labels,
    pn_default_labels,
    sc_default_labels,
    tn_default_visibility_labels,
    pn_default_visibility_labels,
    sc_default_visibility_labels,
    dialog_defaults,
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
    remember_choices_checkbox = window.FindName("RememberChoicesCheckBox")

    prompt_text.Text = "Choose a tab, select the target parameter(s), then run that update:"
    defaults = dialog_defaults or {}
    if remember_choices_checkbox is not None:
        remember_choices_checkbox.IsChecked = bool(defaults.get("remember_choices", True))

    tn_state = _build_true_north_tab_state(
        window, sheet_items, tn_param_groups, yesno_param_groups, yesno_labels,
        tn_prechecked_indices, tn_default_labels, tn_default_visibility_labels,
        bool(defaults.get("tn_set_visibility", True)),
        bool(defaults.get("tn_hide_on", True)),
        bool(defaults.get("tn_all_sheets", True)),
    )
    pn_state = _build_project_north_tab_state(
        window, sheet_items, pn_param_groups, yesno_param_groups, yesno_labels,
        pn_prechecked_indices, pn_default_labels, pn_default_visibility_labels,
        bool(defaults.get("pn_set_visibility", True)),
        bool(defaults.get("pn_hide_on", True)),
        bool(defaults.get("pn_all_sheets", True)),
    )
    sc_state = _build_scale_tab_state(
        window, sheet_items, sc_param_groups, yesno_param_groups, yesno_labels,
        sc_prechecked_indices, sc_default_labels, sc_default_visibility_labels,
        bool(defaults.get("sc_set_visibility", False)),
        bool(defaults.get("sc_hide_on", False)),
        bool(defaults.get("sc_ignore_drafting_views", False)),
        bool(defaults.get("sc_all_sheets", True)),
    )

    tab_states = (tn_state, pn_state, sc_state)
    action_labels = ("Update True North", "Update Project North", "Update Scale")
    try:
        selected_tab_index = int(defaults.get("selected_tab_index", 0))
    except Exception:
        selected_tab_index = 0
    if selected_tab_index < 0 or selected_tab_index >= len(tab_states):
        selected_tab_index = 0
    tab_control.SelectedIndex = selected_tab_index

    def _sync_action_label(sender=None, args=None):
        index = int(tab_control.SelectedIndex)
        if index < 0 or index >= len(action_labels):
            index = 0
        ok_button.Content = action_labels[index]

    tab_control.SelectionChanged += _sync_action_label
    _sync_action_label()

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
        active_index = int(tab_control.SelectedIndex)
        if active_index < 0 or active_index >= len(tab_states):
            active_index = 0
        if not tab_states[active_index]["validate"]():
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

    selected_tab_index = int(tab_control.SelectedIndex)
    tn_result = tn_state["get_result"]()
    pn_result = pn_state["get_result"]()
    sc_result = sc_state["get_result"]()
    tn_result["enabled"] = selected_tab_index == 0
    pn_result["enabled"] = selected_tab_index == 1
    sc_result["enabled"] = selected_tab_index == 2
    return {
        "true_north": tn_result,
        "project_north": pn_result,
        "scale": sc_result,
        "selected_tab_index": selected_tab_index,
        "remember_choices": bool(remember_choices_checkbox is not None and remember_choices_checkbox.IsChecked),
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

    remember_choices = bool(getattr(config, "remember_choices", True))
    use_saved_choices = bool(getattr(config, "remember_choices", False))

    tn_last_sheet_ids = (getattr(config, "tn_sheet_ids", []) or []) if use_saved_choices else []
    pn_last_sheet_ids = (getattr(config, "pn_sheet_ids", []) or []) if use_saved_choices else []
    sc_last_sheet_ids = (getattr(config, "sc_sheet_ids", []) or []) if use_saved_choices else []

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

    yesno_param_groups, yesno_labels = _get_yesno_param_entries(titleblocks)

    def _saved_target_labels(prefix, valid_entries):
        if not use_saved_choices:
            return []
        labels = list(getattr(config, prefix + "_param_labels", []) or [])
        if not labels:
            legacy_name = getattr(config, prefix + "_param_name", "") or ""
            legacy_scope = getattr(config, prefix + "_param_scope", "") or ""
            if legacy_name and legacy_scope:
                labels = ["{} [{}]".format(
                    legacy_name, "Instance" if legacy_scope == "instance" else "Type"
                )]
        return [value for value in labels if value in valid_entries]

    def _saved_visibility_labels(prefix):
        if not use_saved_choices:
            return []
        labels = list(getattr(config, prefix + "_visibility_param_names", []) or [])
        if not labels:
            legacy_name = getattr(config, prefix + "_visibility_param_name", "") or ""
            if legacy_name:
                labels = [legacy_name]
        return [value for value in labels if value in yesno_labels]

    tn_default_labels = _saved_target_labels("tn", tn_param_entries)
    pn_default_labels = _saved_target_labels("pn", tn_param_entries)
    sc_default_labels = _saved_target_labels("sc", sc_param_entries)
    tn_default_visibility_labels = _saved_visibility_labels("tn")
    pn_default_visibility_labels = _saved_visibility_labels("pn")
    sc_default_visibility_labels = _saved_visibility_labels("sc")

    dialog_defaults = {
        "remember_choices": remember_choices,
        "selected_tab_index": int(getattr(config, "selected_tab_index", 0)) if use_saved_choices else 0,
        "tn_set_visibility": bool(getattr(config, "tn_set_visibility", True)) if use_saved_choices else True,
        "pn_set_visibility": bool(getattr(config, "pn_set_visibility", True)) if use_saved_choices else True,
        "sc_set_visibility": bool(getattr(config, "sc_set_visibility", False)) if use_saved_choices else False,
        "tn_hide_on": bool(getattr(config, "tn_hide_on_elev_section", True)) if use_saved_choices else True,
        "pn_hide_on": bool(getattr(config, "pn_hide_on", True)) if use_saved_choices else True,
        "sc_hide_on": bool(getattr(config, "sc_hide_on_no_scale", False)) if use_saved_choices else False,
        "sc_ignore_drafting_views": bool(getattr(config, "sc_ignore_drafting_views", False)) if use_saved_choices else False,
        "tn_all_sheets": bool(getattr(config, "tn_all_sheets", True)) if use_saved_choices else True,
        "pn_all_sheets": bool(getattr(config, "pn_all_sheets", True)) if use_saved_choices else True,
        "sc_all_sheets": bool(getattr(config, "sc_all_sheets", True)) if use_saved_choices else True,
    }

    try:
        dialog_result = _show_titleblock_dialog(
            sheet_items,
            tn_param_groups,
            tn_param_groups,
            sc_param_groups,
            yesno_param_groups,
            yesno_labels,
            _prechecked_for(tn_last_sheet_ids),
            _prechecked_for(pn_last_sheet_ids),
            _prechecked_for(sc_last_sheet_ids),
            tn_default_labels,
            pn_default_labels,
            sc_default_labels,
            tn_default_visibility_labels,
            pn_default_visibility_labels,
            sc_default_visibility_labels,
            dialog_defaults,
        )
    except Exception as ex:
        UI.TaskDialog.Show("Titleblock Updater", "WPF UI error:\n{}".format(str(ex)))
        return

    if not dialog_result:
        uiUtils_alert("Operation cancelled.", "Titleblock Updater")
        return

    tn = dialog_result.get("true_north") or {}
    pn = dialog_result.get("project_north") or {}
    sc = dialog_result.get("scale") or {}

    if not tn.get("enabled") and not pn.get("enabled") and not sc.get("enabled"):
        uiUtils_alert("Nothing selected. Operation cancelled.", "Titleblock Updater")
        return

    titleblocks_by_sheet = {}
    for tb in titleblocks:
        try:
            titleblocks_by_sheet[tb.OwnerViewId] = tb
        except Exception:
            continue

    tn_updated_sheets, tn_failed_sheets, tn_hidden_sheets = [], [], []
    pn_updated_sheets, pn_failed_sheets, pn_hidden_sheets = [], [], []
    sc_updated_sheets, sc_failed_sheets, sc_warning_sheets = [], [], []
    tn_param_labels, pn_param_labels, sc_param_labels = [], [], []
    tn_targets, pn_targets, sc_targets = [], [], []

    t = DB.Transaction(doc, "Update Titleblock Parameters")
    t.Start()
    try:
        if tn.get("enabled"):
            tn_param_labels = list(tn.get("selected_parameters") or [])
            tn_targets = [tn_param_entries[label] for label in tn_param_labels if label in tn_param_entries]
            set_visibility = bool(tn.get("set_visibility"))
            visibility_param_names = list(tn.get("visibility_params") or [])
            hide_on_unsupported_view = bool(tn.get("hide_on"))

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
                    tn_targets,
                    set_visibility, visibility_param_names,
                    hide_on_unsupported_view, true_north_angle,
                    arrow_tag="",
                )
                if status == "updated":
                    tn_updated_sheets.append(sheet_name)
                elif status == "hidden":
                    tn_hidden_sheets.append(message)
                else:
                    tn_failed_sheets.append(message)

        if pn.get("enabled"):
            pn_param_labels = list(pn.get("selected_parameters") or [])
            pn_targets = [tn_param_entries[label] for label in pn_param_labels if label in tn_param_entries]
            set_pn_visibility = bool(pn.get("set_visibility"))
            pn_visibility_param_names = list(pn.get("visibility_params") or [])
            hide_on_unsupported_view_pn = bool(pn.get("hide_on"))

            pn_view_cache = {}
            for i in pn.get("selected_indices", []):
                sheet = sheet_by_index[i]
                sheet_number = sheet.SheetNumber or ""
                sheet_name = sheet.Name or ""
                sheet_label = "{} - {}".format(sheet_number, sheet_name) if sheet_number else sheet_name

                titleblock_instance = titleblocks_by_sheet.get(sheet.Id)
                if not titleblock_instance:
                    pn_failed_sheets.append(sheet_label + " - No titleblock found")
                    continue

                primary_view = None
                for vp_id in sheet.GetAllViewports():
                    viewport = doc.GetElement(vp_id)
                    if not viewport:
                        continue
                    view_id = viewport.ViewId
                    if view_id in pn_view_cache:
                        view = pn_view_cache[view_id]
                    else:
                        view = doc.GetElement(view_id)
                        pn_view_cache[view_id] = view
                    if not view or not hasattr(view, "ViewType"):
                        continue
                    if view.ViewType in (DB.ViewType.Legend, DB.ViewType.DraftingView, DB.ViewType.Schedule):
                        continue
                    primary_view = view
                    break

                project_north_angle = _get_project_north_angle_for_view(primary_view) if primary_view else 0.0
                pn_status, pn_message = _update_arrow_for_sheet(
                    titleblock_instance, sheet, sheet_label, primary_view,
                    pn_targets,
                    set_pn_visibility, pn_visibility_param_names,
                    hide_on_unsupported_view_pn, project_north_angle,
                    arrow_tag="",
                )
                if pn_status == "updated":
                    pn_updated_sheets.append(sheet_name)
                elif pn_status == "hidden":
                    pn_hidden_sheets.append(pn_message)
                else:
                    pn_failed_sheets.append(pn_message)

        if sc.get("enabled"):
            sc_param_labels = list(sc.get("selected_parameters") or [])
            sc_targets = [sc_param_entries[label] for label in sc_param_labels if label in sc_param_entries]
            ignore_drafting_views = bool(sc.get("ignore_drafting_views"))
            set_visibility = bool(sc.get("set_visibility"))
            visibility_param_names = list(sc.get("visibility_params") or [])
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
                    if hide_on_no_scale and set_visibility and visibility_param_names:
                        _set_visibility_parameters(titleblock_instance, visibility_param_names, 0)
                    if only_drafting and ignore_drafting_views:
                        sc_failed_sheets.append(sheet_label + " - Only drafting views found and ignored")
                    else:
                        sc_failed_sheets.append(sheet_label + " - No valid scales found")
                    continue

                sheet_scale_value = list(scales)[0] if len(scales) == 1 else 0

                target_error = _update_scale_targets_for_sheet(
                    sheet, titleblock_instance, sc_targets, sheet_scale_value
                )

                if target_error:
                    sc_failed_sheets.append(sheet_label + " - " + target_error)
                else:
                    if set_visibility and visibility_param_names:
                        _set_visibility_parameters(titleblock_instance, visibility_param_names, 1)
                    sc_updated_sheets.append(sheet_name)

        t.Commit()
    except Exception as e:
        t.RollBack()
        UI.TaskDialog.Show("Error", str(e))
        return

    # Persist settings after a successful commit.
    remember_choices = bool(dialog_result.get("remember_choices"))
    config.remember_choices = remember_choices
    if remember_choices:
        config.selected_tab_index = int(dialog_result.get("selected_tab_index", 0))
        config.tn_enabled = bool(tn.get("enabled"))
        config.tn_sheet_ids = [v for v in (_elem_id_int(sheet_by_index[i].Id) for i in tn.get("selected_indices", [])) if v is not None]
        config.tn_all_sheets = bool(tn.get("all_sheets"))
        config.tn_param_labels = list(tn.get("selected_parameters") or [])
        first_target = tn_param_entries.get(config.tn_param_labels[0]) if config.tn_param_labels else None
        config.tn_param_name = first_target.get("name") if first_target else ""
        config.tn_param_scope = first_target.get("scope") if first_target else ""
        config.tn_set_visibility = bool(tn.get("set_visibility"))
        config.tn_visibility_param_names = list(tn.get("visibility_params") or [])
        config.tn_visibility_param_name = config.tn_visibility_param_names[0] if config.tn_visibility_param_names else ""
        config.tn_hide_on_elev_section = bool(tn.get("hide_on"))

        config.pn_enabled = bool(pn.get("enabled"))
        config.pn_sheet_ids = [v for v in (_elem_id_int(sheet_by_index[i].Id) for i in pn.get("selected_indices", [])) if v is not None]
        config.pn_all_sheets = bool(pn.get("all_sheets"))
        config.pn_param_labels = list(pn.get("selected_parameters") or [])
        first_target = tn_param_entries.get(config.pn_param_labels[0]) if config.pn_param_labels else None
        config.pn_param_name = first_target.get("name") if first_target else ""
        config.pn_param_scope = first_target.get("scope") if first_target else ""
        config.pn_set_visibility = bool(pn.get("set_visibility"))
        config.pn_visibility_param_names = list(pn.get("visibility_params") or [])
        config.pn_visibility_param_name = config.pn_visibility_param_names[0] if config.pn_visibility_param_names else ""
        config.pn_hide_on = bool(pn.get("hide_on"))

        config.sc_enabled = bool(sc.get("enabled"))
        config.sc_sheet_ids = [v for v in (_elem_id_int(sheet_by_index[i].Id) for i in sc.get("selected_indices", [])) if v is not None]
        config.sc_all_sheets = bool(sc.get("all_sheets"))
        config.sc_param_labels = list(sc.get("selected_parameters") or [])
        first_target = sc_param_entries.get(config.sc_param_labels[0]) if config.sc_param_labels else None
        config.sc_param_name = first_target.get("name") if first_target else ""
        config.sc_param_scope = first_target.get("scope") if first_target else ""
        config.sc_set_visibility = bool(sc.get("set_visibility"))
        config.sc_visibility_param_names = list(sc.get("visibility_params") or [])
        config.sc_visibility_param_name = config.sc_visibility_param_names[0] if config.sc_visibility_param_names else ""
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
    if pn.get("enabled"):
        line = "Project North(Construction North): updated {} sheet(s).".format(len(pn_updated_sheets))
        if pn_hidden_sheets:
            line += " Hidden: {}.".format(len(pn_hidden_sheets))
        if pn_failed_sheets:
            line += " Failed/Skipped: {}.".format(len(pn_failed_sheets))
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
        _print_text("Target Parameters: {}".format(", ".join(tn_param_labels)))
        _print_text("Visibility Parameters: {}".format(", ".join(tn.get("visibility_params") or []) or "None"))
        _print_text("Updated: {} sheets".format(len(tn_updated_sheets)))
        _print_text("Hidden: {} sheets".format(len(tn_hidden_sheets)))
        _print_text("Failed/Skipped: {} sheets".format(len(tn_failed_sheets)))
        for name in tn_updated_sheets:
            _print_text(" - {}".format(name))
        for h in tn_hidden_sheets:
            _print_text(" - {}".format(h))
        for f in tn_failed_sheets:
            _print_text(" - {}".format(f))
    if pn.get("enabled"):
        _print_text("")
        _print_text("-- Project North(Construction North) --")
        _print_text("Target Parameters: {}".format(", ".join(pn_param_labels)))
        _print_text("Visibility Parameters: {}".format(", ".join(pn.get("visibility_params") or []) or "None"))
        _print_text("Updated: {} sheets".format(len(pn_updated_sheets)))
        _print_text("Hidden: {} sheets".format(len(pn_hidden_sheets)))
        _print_text("Failed/Skipped: {} sheets".format(len(pn_failed_sheets)))
        for name in pn_updated_sheets:
            _print_text(" - {}".format(name))
        for h in pn_hidden_sheets:
            _print_text(" - {}".format(h))
        for f in pn_failed_sheets:
            _print_text(" - {}".format(f))
    if sc.get("enabled"):
        _print_text("")
        _print_text("-- Scale --")
        _print_text("Target Parameters: {}".format(", ".join(sc_param_labels)))
        _print_text("Visibility Parameters: {}".format(", ".join(sc.get("visibility_params") or []) or "None"))
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
