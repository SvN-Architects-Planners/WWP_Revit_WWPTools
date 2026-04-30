from System import Int64
import clr
import os
import sys
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB, UI

# pyRevit script tools
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
    "SheetScaleUpdater",
    doc=doc,
    legacy_sources=legacy_sources,
)

def _print_md(text):
    try:
        if output:
            output.print_md(text)
            return
    except Exception:
        pass
    try:
        print(text)
        if hasattr(sys.stdout, "flush"):
            sys.stdout.flush()
    except Exception:
        pass

def _elem_id_int(eid):
    if eid is None:
        return None
    try:
        return int(eid.Value)  # Revit 2024+
    except Exception:
        pass
    try:
        return int(eid.IntegerValue)  # Revit 2023-
    except Exception:
        pass
    try:
        return int(eid)
    except Exception:
        return None

# wait dialog removed (lazy loading filter values)

def _is_titleblock(element):
    if not element or not element.Category:
        return False
    category_id = element.Category.Id
    titleblock_builtin_id = int(DB.BuiltInCategory.OST_TitleBlocks)
    # Compare the raw ElementId value when the API exposes it directly.
    if hasattr(category_id, 'IntegerValue'):
        return _elem_id_int(category_id) == titleblock_builtin_id
    # Revit 2026 method: convert to long for ElementId constructor
    try:
        titleblock_id = DB.ElementId(Int64(int(titleblock_builtin_id)))
        return category_id == titleblock_id
    except:
        # Fallback: compare as integers
        try:
            return int(category_id) == titleblock_builtin_id
        except:
            return False

def _collect_titleblocks():
    return list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )

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

def _is_supported_scale_parameter(param):
    try:
        return (
            param.StorageType in (
                DB.StorageType.Double,
                DB.StorageType.Integer,
            )
            and not _is_yes_no_parameter(param)
        )
    except Exception:
        return False

def _get_param_entries(titleblock_instances):
    entries = {}
    for tb in titleblock_instances:
        if not tb:
            continue
        try:
            for p in tb.Parameters:
                if not p or not p.Definition:
                    continue
                if not _is_supported_scale_parameter(p):
                    continue
                name = p.Definition.Name
                if name and "scale" in name.lower():
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
                    if not _is_supported_scale_parameter(p):
                        continue
                    name = p.Definition.Name
                    if name and "scale" in name.lower():
                        key = "{} [Type]".format(name)
                        entries[key] = {"name": name, "scope": "type"}
        except Exception:
            continue
    keys = sorted(entries.keys())
    return keys, entries

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

def _print_text(text=""):
    try:
        print(text)
        if hasattr(sys.stdout, "flush"):
            sys.stdout.flush()
    except Exception:
        pass

def _show_sheet_scale_dialog(
    sheet_items,
    param_labels,
    title,
    prompt,
    prechecked_indices=None,
    default_label=None,
    ignore_drafting_views_default=False,
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

    xaml_path = os.path.join(os.path.dirname(__file__), "SheetScaleUpdaterDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    xaml_reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(xaml_reader)
    if title:
        apply_window_title(window, title)
    else:
        apply_window_title(window, "Sheet Scale Updater")
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass

    prompt_text = window.FindName("PromptText")
    parameter_combo = window.FindName("ParameterCombo")
    search_box = window.FindName("SearchBox")
    sheets_list = window.FindName("SheetsList")
    ignore_drafting_views_checkbox = window.FindName("IgnoreDraftingViewsCheckBox")
    all_sheets_checkbox = window.FindName("AllSheetsCheckBox")
    all_sheets_warning = window.FindName("AllSheetsWarningText")
    validation_text = window.FindName("ValidationText")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")

    prompt_text.Text = prompt or ""
    selected_indices = set(prechecked_indices or [])

    for label in param_labels or []:
        parameter_combo.Items.Add(label)
    if default_label and default_label in (param_labels or []):
        parameter_combo.SelectedItem = default_label
        parameter_combo.Text = default_label
    elif parameter_combo.Items.Count > 0:
        parameter_combo.SelectedIndex = 0
    if ignore_drafting_views_checkbox is not None:
        ignore_drafting_views_checkbox.IsChecked = bool(ignore_drafting_views_default)
    if all_sheets_checkbox is not None:
        all_sheets_checkbox.IsChecked = True
        search_box.IsEnabled = False
        sheets_list.IsEnabled = False

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
        "ignore_drafting_views": bool(
            ignore_drafting_views_checkbox is not None and ignore_drafting_views_checkbox.IsChecked
        ),
        "selected_parameter": selected_param,
    }

def _element_id_int(element_id):
    if element_id is None:
        return None
    if hasattr(element_id, "IntegerValue"):
        try:
            return int(_elem_id_int(element_id))
        except Exception:
            pass
    if hasattr(element_id, "Value"):
        try:
            return int(element_id.Value)
        except Exception:
            pass
    try:
        return int(element_id)
    except Exception:
        return None

def main():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheets.sort(key=lambda s: (s.SheetNumber or "", s.Name or ""))
    if not sheets:
        UI.TaskDialog.Show("Sheet Scale Updater", "No sheets found.")
        return

    updated_count = 0
    updated_sheets = []
    failed_sheets = []
    warning_sheets = []

    active_view = doc.ActiveView
    current_sheet_id_val = None
    if active_view and hasattr(active_view, "ViewType") and active_view.ViewType == DB.ViewType.DrawingSheet:
        current_sheet_id_val = _element_id_int(active_view.Id)

    if current_sheet_id_val is not None:
        current_first = []
        remaining = []
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
        item_label = "{} - {}".format(number, name)
        if current_sheet_id_val is not None and _element_id_int(sheet.Id) == current_sheet_id_val:
            item_label = "[Current Sheet] " + item_label
        sheet_items.append(item_label)
        sheet_by_index.append(sheet)

    last_sheet_ids = getattr(config, "sheet_ids", []) or []
    last_param_name = getattr(config, "sheet_scale_param_name", "") or ""
    last_param_scope = getattr(config, "sheet_scale_param_scope", "") or ""
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
        UI.TaskDialog.Show("Sheet Scale Updater", "No titleblock found on any sheet.")
        return

    param_labels, param_entries = _get_param_entries(titleblocks)
    if not param_labels:
        UI.TaskDialog.Show(
            "Sheet Scale Updater",
            "No parameters found on selected titleblock instances.",
        )
        return

    target_param_name = last_param_name
    target_param_scope = last_param_scope
    target_label = None
    default_label = None
    if target_param_name and target_param_scope:
        default_label = "{} [{}]".format(target_param_name, "Instance" if target_param_scope == "instance" else "Type")
    try:
        dialog_result = _show_sheet_scale_dialog(
            sheet_items,
            param_labels,
            title="Sheet Scale Updater",
            prompt="Select sheets to update and choose the target parameter:",
            prechecked_indices=prechecked_indices,
            default_label=default_label,
            ignore_drafting_views_default=False,
        )
    except Exception as ex:
        UI.TaskDialog.Show("Sheet Scale Updater", "WPF UI error:\n{}".format(str(ex)))
        return

    if not dialog_result:
        uiUtils_alert("Operation cancelled.", "Sheet Scale Updater")
        return

    selected_indices = dialog_result.get("selected_indices", [])
    if not selected_indices:
        uiUtils_alert("No sheets selected. Operation cancelled.", "Sheet Scale Updater")
        return

    selected_sheets = [sheet_by_index[i] for i in selected_indices]
    target_label = dialog_result.get("selected_parameter", "")
    if not target_label:
        uiUtils_alert("No parameter selected. Operation cancelled.", "Sheet Scale Updater")
        return

    entry = param_entries.get(target_label)
    target_param_name = entry["name"] if entry else target_label
    target_param_scope = entry["scope"] if entry else "instance"
    ignore_drafting_views = bool(dialog_result.get("ignore_drafting_views", False))

    config.sheet_ids = [v for v in (_element_id_int(s.Id) for s in selected_sheets) if v is not None]
    config.sheet_scale_param_name = target_param_name
    config.sheet_scale_param_scope = target_param_scope
    save_config()

    titleblocks_by_sheet = {}
    for tb in titleblocks:
        try:
            titleblocks_by_sheet[tb.OwnerViewId] = tb
        except Exception:
            continue

    t = DB.Transaction(doc, "Update Sheet Scale")
    t.Start()
    try:
        view_scale_cache = {}
        for sheet in selected_sheets:
            sheet_number = sheet.SheetNumber or ""
            sheet_name = sheet.Name
            sheet_label = "{} - {}".format(sheet_number, sheet_name) if sheet_number else sheet_name
            sheet_debug = {"sheet": sheet_name}

            titleblock_instance = titleblocks_by_sheet.get(sheet.Id)
            if not titleblock_instance:
                sheet_debug["error"] = "No titleblock found"
                failed_sheets.append(sheet_label + " - No titleblock found")
                continue

            sheet_debug["titleblock_found"] = True
            viewport_ids = sheet.GetAllViewports()
            if viewport_ids.Count == 0:
                sheet_debug["error"] = "No viewports"
                failed_sheets.append(sheet_label + " - No viewports")
                continue

            sheet_debug["viewport_count"] = viewport_ids.Count
            scales = set()
            scale_details = []
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
                if sval is not None:
                    scale_details.append(sval)
                    if sval > 0:
                        scales.add(sval)

            sheet_debug["scales_read"] = sorted(list(scales))
            sheet_debug["all_viewport_scales"] = scale_details
            if legend_views_skipped:
                sheet_debug["legend_views_skipped"] = legend_views_skipped
            if drafting_views_skipped:
                sheet_debug["drafting_views_skipped"] = drafting_views_skipped
            if non_legend_view_count and non_legend_view_count == non_legend_drafting_count:
                warning_message = sheet_label + " - Only drafting views found on sheet"
                if ignore_drafting_views:
                    warning_message += " (ignored for scale calculation)"
                warning_sheets.append(warning_message)
                sheet_debug["warning"] = warning_message

            if not scales:
                if non_legend_view_count and non_legend_view_count == non_legend_drafting_count and ignore_drafting_views:
                    sheet_debug["error"] = "Only drafting views found and ignored"
                    failed_sheets.append(sheet_label + " - Only drafting views found and ignored")
                else:
                    sheet_debug["error"] = "No valid scales found"
                    failed_sheets.append(sheet_label + " - No valid scales found")
                continue

            if len(scales) == 1:
                sheet_scale_value = list(scales)[0]
                sheet_debug["scale_selection"] = "single scale"
            else:
                sheet_scale_value = 0
                sheet_debug["multiple_scales"] = True
                sheet_debug["scale_selection"] = "multiple scales detected - setting to 0"
                override_param = titleblock_instance.LookupParameter("Show Scale Override")
                sheet_debug["override_param_exists"] = override_param is not None
                if override_param:
                    sheet_debug["override_value"] = override_param.AsInteger()

            sheet_debug["scale_to_write"] = sheet_scale_value

            sheet_scale_param, resolved_target_owner = _resolve_target_parameter(
                sheet,
                titleblock_instance,
                target_param_name,
                target_param_scope,
            )
            sheet_debug["target_param_name"] = target_param_name
            sheet_debug["target_param_scope"] = target_param_scope
            sheet_debug["target_param_owner"] = resolved_target_owner
            sheet_debug["sheet_scale_param_exists"] = sheet_scale_param is not None

            if not sheet_scale_param:
                sheet_debug["error"] = "Target parameter missing"
                failed_sheets.append(sheet_label + " - Target parameter missing: {}".format(target_param_name))
                continue

            sheet_debug["param_storage_type"] = str(sheet_scale_param.StorageType)
            try:
                if sheet_scale_param.IsReadOnly:
                    owner_label = resolved_target_owner or "resolved target"
                    sheet_debug["error"] = "Parameter is read-only ({})".format(owner_label)
                    failed_sheets.append(sheet_label + " - Parameter is read-only ({})".format(owner_label))
                elif sheet_scale_param.StorageType == DB.StorageType.Double:
                    sheet_scale_param.Set(float(sheet_scale_value))
                    sheet_debug["written_as"] = "Double"
                    sheet_debug["status"] = "SUCCESS"
                    updated_sheets.append(sheet_name)
                    updated_count += 1
                elif sheet_scale_param.StorageType == DB.StorageType.Integer:
                    sheet_scale_param.Set(int(sheet_scale_value))
                    sheet_debug["written_as"] = "Integer"
                    sheet_debug["status"] = "SUCCESS"
                    updated_sheets.append(sheet_name)
                    updated_count += 1
                elif sheet_scale_param.StorageType == DB.StorageType.String:
                    sheet_scale_param.Set(str(sheet_scale_value))
                    sheet_debug["written_as"] = "String"
                    sheet_debug["status"] = "SUCCESS"
                    updated_sheets.append(sheet_name)
                    updated_count += 1
                else:
                    sheet_scale_param.Set(sheet_scale_value)
                    sheet_debug["written_as"] = str(sheet_scale_param.StorageType)
                    sheet_debug["status"] = "SUCCESS"
                    updated_sheets.append(sheet_name)
                    updated_count += 1
            except Exception as e:
                sheet_debug["error"] = "Failed to set parameter: {}".format(str(e))
                failed_sheets.append(sheet_label + " - Failed to set parameter: {}".format(str(e)))

        t.Commit()
    except Exception as e:
        t.RollBack()
        UI.TaskDialog.Show("Error", str(e))
        return

    msg = "Successfully updated {} sheet(s).".format(updated_count)
    if warning_sheets:
        msg += "\nWarnings: {}".format(len(warning_sheets))
    if failed_sheets:
        msg += "\n\nFailed/Skipped: {}".format(len(failed_sheets))
    UI.TaskDialog.Show("Sheet Scale Updater", msg)

    _print_text("")
    _print_text("Sheet Scale Updater Report")
    _print_text("Developed by: Jason Tian")
    _print_text("Updated: {} sheets".format(updated_count))
    _print_text("Warnings: {} sheets".format(len(warning_sheets)))
    _print_text("Failed/Skipped: {} sheets".format(len(failed_sheets)))
    _print_text("Target Parameter: {} ({})".format(target_param_name, target_param_scope or "instance"))
    _print_text("Ignore Drafting Views: {}".format("Yes" if ignore_drafting_views else "No"))
    if updated_sheets:
        _print_text("")
        _print_text("Changed Sheets:")
        for sheet_name in updated_sheets:
            _print_text(" - {}".format(sheet_name))
    else:
        _print_text("")
        _print_text("Changed Sheets:")
        _print_text(" - None")

    if failed_sheets:
        _print_text("")
        _print_text("Failed/Skipped Sheets:")
        for failure in failed_sheets:
            _print_text(" - {}".format(failure))

    if warning_sheets:
        _print_text("")
        _print_text("Warnings:")
        for warning in warning_sheets:
            _print_text(" - {}".format(warning))

main()
