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
    "TrueNorthUpdater",
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

def _is_numeric_parameter(param):
    try:
        return (
            param.StorageType in (DB.StorageType.Double, DB.StorageType.Integer)
            and not _is_yes_no_parameter(param)
        )
    except Exception:
        return False

def _is_angle_parameter(param):
    if not param or not getattr(param, "Definition", None):
        return False
    definition = param.Definition
    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            return definition.GetDataType() == DB.SpecTypeId.Angle
    except Exception:
        pass
    try:
        if hasattr(definition, "ParameterType") and definition.ParameterType == DB.ParameterType.Angle:
            return True
    except Exception:
        pass
    return False

def _collect_titleblocks():
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )

def _get_param_entries(titleblock_instances):
    """Return all angle parameters on titleblock instances and types."""
    entries = {}
    for tb in titleblock_instances:
        if not tb:
            continue
        try:
            for p in tb.Parameters:
                if not p or not p.Definition:
                    continue
                if not _is_angle_parameter(p):
                    continue
                name = p.Definition.Name
                if name:
                    key = "{} [Instance]".format(name)
                    entries[key] = {"name": name, "scope": "instance"}
        except Exception:
            continue
        try:
            symbol = tb.Symbol if hasattr(tb, "Symbol") else None
            if symbol:
                for p in symbol.Parameters:
                    if not p or not p.Definition:
                        continue
                    if not _is_angle_parameter(p):
                        continue
                    name = p.Definition.Name
                    if name:
                        key = "{} [Type]".format(name)
                        entries[key] = {"name": name, "scope": "type"}
        except Exception:
            continue
    keys = sorted(entries.keys())
    return keys, entries

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

    # True North direction in world XY space
    tn = DB.XYZ(-math.sin(angle_rad), math.cos(angle_rad), 0)

    try:
        up = view.UpDirection
        right = view.RightDirection
        north_up = tn.DotProduct(up)
        north_right = tn.DotProduct(right)
        # Negate north_right to get CCW angle from view up (matches Revit's convention)
        angle_deg = math.degrees(math.atan2(-north_right, north_up))
        return round(angle_deg % 360.0, 4)
    except Exception:
        return round(math.degrees(angle_rad) % 360.0, 4)

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

def _element_id_int(element_id):
    return _elem_id_int(element_id)

def _show_true_north_dialog(
    sheet_items,
    param_labels,
    title,
    prompt,
    prechecked_indices=None,
    default_label=None,
    yesno_param_labels=None,
    default_visibility_label=None,
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
    from System.Windows.Controls import ListBoxItem
    from System.Windows.Interop import WindowInteropHelper
    from System.Windows.Markup import XamlReader
    from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
    from System.Xml import XmlReader

    xaml_path = os.path.join(os.path.dirname(__file__), "TrueNorthUpdaterDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    xaml_reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(xaml_reader)
    apply_window_title(window, title or "True North Updater")

    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass

    prompt_text = window.FindName("PromptText")
    parameter_combo = window.FindName("ParameterCombo")
    search_box = window.FindName("SearchBox")
    sheets_list = window.FindName("SheetsList")
    all_sheets_checkbox = window.FindName("AllSheetsCheckBox")
    all_sheets_warning = window.FindName("AllSheetsWarningText")
    validation_text = window.FindName("ValidationText")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")
    set_visibility_checkbox = window.FindName("SetVisibilityCheckBox")
    visibility_param_combo = window.FindName("VisibilityParamCombo")
    hide_elev_section_checkbox = window.FindName("HideElevSectionCheckBox")

    prompt_text.Text = prompt or ""
    selected_indices = set(prechecked_indices or [])

    for label in param_labels or []:
        parameter_combo.Items.Add(label)
    if default_label and default_label in (param_labels or []):
        parameter_combo.SelectedItem = default_label
        parameter_combo.Text = default_label
    elif parameter_combo.Items.Count > 0:
        parameter_combo.SelectedIndex = 0

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = True
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False

    for label in (yesno_param_labels or []):
        visibility_param_combo.Items.Add(label)
    if default_visibility_label and default_visibility_label in (yesno_param_labels or []):
        visibility_param_combo.SelectedItem = default_visibility_label
    elif visibility_param_combo.Items.Count > 0:
        visibility_param_combo.SelectedIndex = 0
    if set_visibility_checkbox is not None:
        set_visibility_checkbox.IsChecked = False
    if hide_elev_section_checkbox is not None:
        hide_elev_section_checkbox.IsChecked = False
        hide_elev_section_checkbox.IsEnabled = False

    def _on_visibility_checked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = True
        if hide_elev_section_checkbox is not None:
            hide_elev_section_checkbox.IsEnabled = True

    def _on_visibility_unchecked(sender, args):
        if visibility_param_combo is not None:
            visibility_param_combo.IsEnabled = False
        if hide_elev_section_checkbox is not None:
            hide_elev_section_checkbox.IsChecked = False
            hide_elev_section_checkbox.IsEnabled = False

    if set_visibility_checkbox is not None:
        set_visibility_checkbox.Checked += _on_visibility_checked
        set_visibility_checkbox.Unchecked += _on_visibility_unchecked

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

    def _set_validation(message):
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

    def _on_ok(sender, args):
        selected_param = str(parameter_combo.Text or "").strip()
        use_all = all_sheets_checkbox is not None and all_sheets_checkbox.IsChecked
        if use_all:
            selected_indices.clear()
            selected_indices.update(range(len(sheet_items)))
        else:
            _sync_selected()
        if not selected_indices:
            _set_validation("Select at least one sheet.")
            return
        if not selected_param:
            _set_validation("Select a parameter.")
            return
        if selected_param not in (param_labels or []):
            _set_validation("Select a valid parameter from the dropdown.")
            return
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    if all_sheets_checkbox is not None:
        all_sheets_checkbox.Checked += _on_all_sheets_checked
        all_sheets_checkbox.Unchecked += _on_all_sheets_unchecked
    search_box.TextChanged += _on_search_changed
    sheets_list.SelectionChanged += _on_selection_changed
    ok_button.Click += _on_ok
    cancel_button.Click += _on_cancel

    _render_sheets()

    if window.ShowDialog() != True:
        return None

    selected_param = str(parameter_combo.Text or "").strip()
    return {
        "selected_indices": sorted(selected_indices),
        "selected_parameter": selected_param,
        "set_visibility": bool(set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked),
        "visibility_param": str(visibility_param_combo.Text or "").strip() if (set_visibility_checkbox is not None and set_visibility_checkbox.IsChecked) else "",
        "hide_on_elev_section": bool(hide_elev_section_checkbox is not None and hide_elev_section_checkbox.IsChecked),
    }

def main():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheets.sort(key=lambda s: (s.SheetNumber or "", s.Name or ""))
    if not sheets:
        UI.TaskDialog.Show("True North Updater", "No sheets found.")
        return

    active_view = doc.ActiveView
    current_sheet_id_val = None
    if active_view and hasattr(active_view, "ViewType") and active_view.ViewType == DB.ViewType.DrawingSheet:
        current_sheet_id_val = _element_id_int(active_view.Id)

    if current_sheet_id_val is not None:
        current_first, remaining = [], []
        for sheet in sheets:
            if _element_id_int(sheet.Id) == current_sheet_id_val:
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
        if current_sheet_id_val is not None and _element_id_int(sheet.Id) == current_sheet_id_val:
            label = "[Current Sheet] " + label
        sheet_items.append(label)
        sheet_by_index.append(sheet)

    last_sheet_ids = getattr(config, "sheet_ids", []) or []
    last_param_name = getattr(config, "true_north_param_name", "") or ""
    last_param_scope = getattr(config, "true_north_param_scope", "") or ""
    prechecked_indices = []
    if last_sheet_ids:
        for i, s in enumerate(sheet_by_index):
            sheet_id_val = _element_id_int(s.Id)
            if sheet_id_val is not None and sheet_id_val in last_sheet_ids:
                prechecked_indices.append(i)
    elif current_sheet_id_val is not None:
        for i, s in enumerate(sheet_by_index):
            if _element_id_int(s.Id) == current_sheet_id_val:
                prechecked_indices.append(i)
                break

    titleblocks = _collect_titleblocks()
    if not titleblocks:
        UI.TaskDialog.Show("True North Updater", "No titleblock found on any sheet.")
        return

    param_labels, param_entries = _get_param_entries(titleblocks)
    if not param_labels:
        UI.TaskDialog.Show("True North Updater", "No numeric parameters found on titleblock instances.")
        return

    yesno_param_labels = _get_yesno_param_entries(titleblocks)
    last_visibility_param = getattr(config, "visibility_param_name", "") or ""

    default_label = None
    if last_param_name and last_param_scope:
        default_label = "{} [{}]".format(last_param_name, "Instance" if last_param_scope == "instance" else "Type")

    try:
        dialog_result = _show_true_north_dialog(
            sheet_items,
            param_labels,
            title="True North Updater",
            prompt="Select sheets to update and choose the target angle parameter:",
            prechecked_indices=prechecked_indices,
            default_label=default_label,
            yesno_param_labels=yesno_param_labels,
            default_visibility_label=last_visibility_param if last_visibility_param in yesno_param_labels else None,
        )
    except Exception as ex:
        UI.TaskDialog.Show("True North Updater", "WPF UI error:\n{}".format(str(ex)))
        return

    if not dialog_result:
        uiUtils_alert("Operation cancelled.", "True North Updater")
        return

    selected_indices = dialog_result.get("selected_indices", [])
    if not selected_indices:
        uiUtils_alert("No sheets selected. Operation cancelled.", "True North Updater")
        return

    selected_sheets = [sheet_by_index[i] for i in selected_indices]
    target_label = dialog_result.get("selected_parameter", "")
    if not target_label:
        uiUtils_alert("No parameter selected. Operation cancelled.", "True North Updater")
        return

    entry = param_entries.get(target_label)
    target_param_name = entry["name"] if entry else target_label
    target_param_scope = entry["scope"] if entry else "instance"
    set_visibility = bool(dialog_result.get("set_visibility", False))
    visibility_param_name = dialog_result.get("visibility_param", "")
    hide_on_elev_section = bool(dialog_result.get("hide_on_elev_section", False))

    config.sheet_ids = [v for v in (_element_id_int(s.Id) for s in selected_sheets) if v is not None]
    config.true_north_param_name = target_param_name
    config.true_north_param_scope = target_param_scope
    config.visibility_param_name = visibility_param_name if set_visibility else ""
    config.hide_on_elev_section = hide_on_elev_section
    save_config()

    titleblocks_by_sheet = {}
    for tb in titleblocks:
        try:
            titleblocks_by_sheet[tb.OwnerViewId] = tb
        except Exception:
            continue

    updated_count = 0
    updated_sheets = []
    failed_sheets = []
    hidden_sheets = []

    t = DB.Transaction(doc, "Update True North Angle")
    t.Start()
    try:
        view_cache = {}
        for sheet in selected_sheets:
            sheet_number = sheet.SheetNumber or ""
            sheet_name = sheet.Name or ""
            sheet_label = "{} - {}".format(sheet_number, sheet_name) if sheet_number else sheet_name

            titleblock_instance = titleblocks_by_sheet.get(sheet.Id)
            if not titleblock_instance:
                failed_sheets.append(sheet_label + " - No titleblock found")
                continue

            # Find the primary viewport (first non-legend, non-drafting view)
            primary_view = None
            viewport_ids = sheet.GetAllViewports()
            for vp_id in viewport_ids:
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

            if primary_view is None:
                if hide_on_elev_section and set_visibility and visibility_param_name:
                    try:
                        vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                        if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                            vis_p.Set(0)
                        hidden_sheets.append(sheet_label + " - No plan view found (north arrow hidden)")
                        continue
                    except Exception:
                        pass
                failed_sheets.append(sheet_label + " - No suitable viewport found")
                continue

            _HIDE_VIEW_TYPES = (DB.ViewType.Elevation, DB.ViewType.Section)
            if hide_on_elev_section and primary_view.ViewType in _HIDE_VIEW_TYPES:
                if set_visibility and visibility_param_name:
                    try:
                        vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                        if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                            vis_p.Set(0)
                    except Exception:
                        pass
                hidden_sheets.append(sheet_label + " - Primary view is {}, north arrow hidden".format(
                    "elevation" if primary_view.ViewType == DB.ViewType.Elevation else "section"
                ))
                continue

            angle_value = _get_true_north_angle_for_view(doc, primary_view)

            param, resolved_owner = _resolve_target_parameter(
                sheet, titleblock_instance, target_param_name, target_param_scope
            )

            if not param:
                failed_sheets.append(sheet_label + " - Target parameter missing: {}".format(target_param_name))
                continue

            try:
                if param.IsReadOnly:
                    failed_sheets.append(sheet_label + " - Parameter is read-only ({})".format(resolved_owner or ""))
                elif param.StorageType == DB.StorageType.Double:
                    value_to_set = math.radians(angle_value) if _is_angle_parameter(param) else float(angle_value)
                    param.Set(value_to_set)
                    if set_visibility and visibility_param_name:
                        vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                        if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                            vis_p.Set(1)
                    updated_sheets.append(sheet_name)
                    updated_count += 1
                elif param.StorageType == DB.StorageType.Integer:
                    param.Set(int(round(angle_value)))
                    if set_visibility and visibility_param_name:
                        vis_p = titleblock_instance.LookupParameter(visibility_param_name)
                        if vis_p and not vis_p.IsReadOnly and vis_p.StorageType == DB.StorageType.Integer:
                            vis_p.Set(1)
                    updated_sheets.append(sheet_name)
                    updated_count += 1
                else:
                    failed_sheets.append(sheet_label + " - Unsupported storage type: {}".format(param.StorageType))
            except Exception as e:
                failed_sheets.append(sheet_label + " - Failed to set parameter: {}".format(str(e)))

        t.Commit()
    except Exception as e:
        t.RollBack()
        UI.TaskDialog.Show("Error", str(e))
        return

    msg = "Successfully updated {} sheet(s).".format(updated_count)
    if hidden_sheets:
        msg += "\nHidden (north arrow): {}".format(len(hidden_sheets))
    if failed_sheets:
        msg += "\n\nFailed/Skipped: {}".format(len(failed_sheets))
    UI.TaskDialog.Show("True North Updater", msg)

    _print_text("")
    _print_text("True North Updater Report")
    _print_text("Developed by: Jason Tian")
    _print_text("Updated: {} sheets".format(updated_count))
    _print_text("Hidden: {} sheets".format(len(hidden_sheets)))
    _print_text("Failed/Skipped: {} sheets".format(len(failed_sheets)))
    _print_text("Target Parameter: {} ({})".format(target_param_name, target_param_scope or "instance"))
    if updated_sheets:
        _print_text("")
        _print_text("Changed Sheets:")
        for name in updated_sheets:
            _print_text(" - {}".format(name))
    if hidden_sheets:
        _print_text("")
        _print_text("Hidden Sheets (north arrow set to No):")
        for h in hidden_sheets:
            _print_text(" - {}".format(h))
    if failed_sheets:
        _print_text("")
        _print_text("Failed/Skipped Sheets:")
        for f in failed_sheets:
            _print_text(" - {}".format(f))

main()
