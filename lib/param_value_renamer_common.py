import os
import re
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import DB
from System.IO import File

from System.Windows.Controls import ComboBoxItem, SelectionChangedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

import WWP_uiUtils as ui


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document if uidoc else None

TOOL_TITLE_SELECTION = "Rename Param Value(by Selections)"
TOOL_TITLE_CATEGORY  = "Rename Param Value(by Category)"


def _elem_id_int(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def _is_in_group(element):
    try:
        gid = element.GroupId
        return gid is not None and gid != DB.ElementId.InvalidElementId
    except Exception:
        return False


def get_categories_with_elements(current_doc):
    cats = {}
    try:
        all_elems = (DB.FilteredElementCollector(current_doc)
                     .WhereElementIsNotElementType()
                     .ToElements())
        for e in all_elems:
            try:
                cat = e.Category
                if cat is not None and cat.CategoryType == DB.CategoryType.Model:
                    if cat.Name not in cats:
                        cats[cat.Name] = cat.Id
            except Exception:
                pass
    except Exception:
        pass
    return sorted(cats.items())  # [(name, ElementId), ...]


def collect_elements_by_category(current_doc, cat_id, ignore_groups):
    try:
        elements = list(
            DB.FilteredElementCollector(current_doc)
            .OfCategoryId(cat_id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        if ignore_groups:
            elements = [e for e in elements if not _is_in_group(e)]
        return elements
    except Exception:
        return []


def collect_elements_from_selection(current_doc, ignore_groups):
    try:
        current_uidoc = __revit__.ActiveUIDocument
        selected_ids = list(current_uidoc.Selection.GetElementIds())
        elements = []
        for eid in selected_ids:
            e = current_doc.GetElement(eid)
            if e is None or isinstance(e, DB.ElementType):
                continue
            elements.append(e)
        if ignore_groups:
            elements = [e for e in elements if not _is_in_group(e)]
        return elements
    except Exception:
        return []


def get_string_parameter_names(elements):
    names = set()
    for e in elements:
        try:
            for p in e.Parameters:
                try:
                    if p.StorageType == DB.StorageType.String and not p.IsReadOnly:
                        names.add(p.Definition.Name)
                except Exception:
                    pass
        except Exception:
            pass
    return sorted(names)


def _get_param_value(element, param_name):
    p = element.LookupParameter(param_name)
    if p is None or p.StorageType != DB.StorageType.String or p.IsReadOnly:
        return None
    val = p.AsString()
    return val if val is not None else ""


def _set_param_value(element, param_name, new_value):
    p = element.LookupParameter(param_name)
    if p is None or p.StorageType != DB.StorageType.String or p.IsReadOnly:
        raise Exception("Parameter '{}' not found or read-only".format(param_name))
    p.Set(new_value)


def _build_new_value(current, find_text, replace_text, prefix, suffix):
    new_val = current
    if find_text:
        new_val = re.sub(re.escape(find_text), replace_text, new_val, flags=re.IGNORECASE)
    if prefix:
        new_val = "{}{}".format(prefix, new_val)
    if suffix:
        new_val = "{}{}".format(new_val, suffix)
    return new_val


def plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix):
    planned = []
    skipped = []
    for e in elements:
        old_val = _get_param_value(e, param_name)
        if old_val is None:
            continue
        new_val = _build_new_value(old_val, find_text, replace_text, prefix, suffix).strip()
        if new_val == old_val:
            continue
        if len(new_val) > 255:
            skipped.append((old_val, new_val, "value too long"))
            continue
        planned.append((e, old_val, new_val))
    return planned, skipped


def apply_param_renames(current_doc, planned, param_name, transaction_title):
    renamed = []
    failed = []
    t = DB.Transaction(current_doc, "{}: {}".format(transaction_title, param_name))
    try:
        t.Start()
        for element, old_val, new_val in planned:
            try:
                _set_param_value(element, param_name, new_val)
                renamed.append((old_val, new_val))
            except Exception as ex:
                failed.append((old_val, new_val, str(ex)))
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return [], failed + [("<transaction>", "<commit>", str(ex))]
    return renamed, failed


def _ensure_theme(lib_path):
    try:
        ver = int(str(__revit__.Application.VersionNumber))
    except Exception:
        ver = None
    dll_name = "WWPTools.WpfUI.net8.0-windows.dll" if ver and ver >= 2025 else "WWPTools.WpfUI.net48.dll"
    dll_path = os.path.join(lib_path, dll_name)
    if not os.path.isfile(dll_path):
        return
    try:
        if hasattr(clr, "AddReferenceToFileAndPath"):
            clr.AddReferenceToFileAndPath(dll_path)
        else:
            clr.AddReference(dll_path)
    except Exception:
        pass


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle if uidoc else 0
    except Exception:
        pass


def _load_xaml(script_dir):
    xaml_path = os.path.join(script_dir, "ParamRenamer.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing XAML file: {}".format(xaml_path))
    return XamlReader.Parse(File.ReadAllText(xaml_path))


def _populate_param_combo(cmb_param, elements):
    cmb_param.Items.Clear()
    if not elements:
        item = ComboBoxItem()
        item.Content = "(no elements found)"
        item.IsEnabled = False
        cmb_param.Items.Add(item)
        return []
    names = get_string_parameter_names(elements)
    if not names:
        item = ComboBoxItem()
        item.Content = "(no editable text parameters)"
        item.IsEnabled = False
        cmb_param.Items.Add(item)
        return []
    for name in names:
        cbi = ComboBoxItem()
        cbi.Content = name
        cmb_param.Items.Add(cbi)
    if cmb_param.Items.Count > 0:
        cmb_param.SelectedIndex = 0
    return names


def show_dialog_selection(script_dir, lib_path):
    _ensure_theme(lib_path)
    window = _load_xaml(script_dir)
    _set_owner(window)

    title = TOOL_TITLE_SELECTION
    window.Title = title
    window.FindName("TxtHeader").Text = title
    window.FindName("TxtSubtitle").Text = "Rename instance parameter values for the current Revit selection."

    lbl_selector = window.FindName("LblSelector")
    lbl_selector.Content = "Selection:"
    cmb_category = window.FindName("CmbCategory")
    cmb_category.IsEnabled = False
    sel_item = ComboBoxItem()
    sel_item.Content = "Current Selection"
    cmb_category.Items.Add(sel_item)
    cmb_category.SelectedIndex = 0

    window.FindName("TxtSource").Text = "Source: Current selection"

    cmb_param = window.FindName("CmbParameter")
    chk_ignore = window.FindName("ChkIgnoreGroups")
    txt_find    = window.FindName("TxtFind")
    txt_replace = window.FindName("TxtReplace")
    txt_prefix  = window.FindName("TxtPrefix")
    txt_suffix  = window.FindName("TxtSuffix")
    btn_cancel  = window.FindName("BtnCancel")
    btn_apply   = window.FindName("BtnApply")

    # Populate parameters from all selected instances (no group filter at this stage)
    initial_elements = collect_elements_from_selection(doc, ignore_groups=False)
    param_names = _populate_param_combo(cmb_param, initial_elements)

    result = [None]

    def _on_apply(sender, args):
        idx = cmb_param.SelectedIndex
        if idx < 0 or idx >= len(param_names):
            ui.uiUtils_alert("No editable text parameters available for the current selection.", title=title)
            return
        result[0] = {
            "param_name": param_names[idx],
            "find":    txt_find.Text or "",
            "replace": txt_replace.Text or "",
            "prefix":  txt_prefix.Text or "",
            "suffix":  txt_suffix.Text or "",
            "ignore_groups": chk_ignore.IsChecked == True,
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    btn_apply.Click  += _on_apply
    btn_cancel.Click += _on_cancel

    if window.ShowDialog() != True:
        return None
    return result[0]


def show_dialog_category(script_dir, lib_path):
    _ensure_theme(lib_path)
    window = _load_xaml(script_dir)
    _set_owner(window)

    title = TOOL_TITLE_CATEGORY
    window.Title = title
    window.FindName("TxtHeader").Text = title
    window.FindName("TxtSubtitle").Text = "Rename instance parameter values across a category."

    lbl_selector = window.FindName("LblSelector")
    lbl_selector.Content = "Category:"
    cmb_category = window.FindName("CmbCategory")
    cmb_category.IsEnabled = True

    window.FindName("TxtSource").Text = "Source: All elements in document"

    cmb_param   = window.FindName("CmbParameter")
    chk_ignore  = window.FindName("ChkIgnoreGroups")
    txt_find    = window.FindName("TxtFind")
    txt_replace = window.FindName("TxtReplace")
    txt_prefix  = window.FindName("TxtPrefix")
    txt_suffix  = window.FindName("TxtSuffix")
    btn_cancel  = window.FindName("BtnCancel")
    btn_apply   = window.FindName("BtnApply")

    cat_list = get_categories_with_elements(doc)  # [(name, ElementId), ...]
    cat_ids  = []
    cat_names = []
    for cat_name, cat_id in cat_list:
        cbi = ComboBoxItem()
        cbi.Content = cat_name
        cmb_category.Items.Add(cbi)
        cat_ids.append(cat_id)
        cat_names.append(cat_name)

    param_names = []

    def _refresh_params():
        idx = cmb_category.SelectedIndex
        if idx < 0 or idx >= len(cat_ids):
            cmb_param.Items.Clear()
            del param_names[:]
            return
        elements = collect_elements_by_category(doc, cat_ids[idx], ignore_groups=False)
        names = _populate_param_combo(cmb_param, elements)
        del param_names[:]
        param_names.extend(names)

    if cmb_category.Items.Count > 0:
        cmb_category.SelectedIndex = 0
        _refresh_params()

    result = [None]

    def _on_category_changed(sender, args):
        _refresh_params()

    def _on_apply(sender, args):
        cat_idx   = cmb_category.SelectedIndex
        param_idx = cmb_param.SelectedIndex
        if cat_idx < 0 or cat_idx >= len(cat_names):
            ui.uiUtils_alert("Please select a category.", title=title)
            return
        if param_idx < 0 or param_idx >= len(param_names):
            ui.uiUtils_alert("No editable text parameters available for the selected category.", title=title)
            return
        result[0] = {
            "cat_id":    cat_ids[cat_idx],
            "cat_name":  cat_names[cat_idx],
            "param_name": param_names[param_idx],
            "find":    txt_find.Text or "",
            "replace": txt_replace.Text or "",
            "prefix":  txt_prefix.Text or "",
            "suffix":  txt_suffix.Text or "",
            "ignore_groups": chk_ignore.IsChecked == True,
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    cmb_category.SelectionChanged += SelectionChangedEventHandler(_on_category_changed)
    btn_apply.Click  += _on_apply
    btn_cancel.Click += _on_cancel

    if window.ShowDialog() != True:
        return None
    return result[0]


def _show_preview_and_apply(doc, planned, skipped, scope_lines, param_name, title, transaction_title):
    lines = scope_lines + [
        "To update: {}".format(len(planned)),
        "Skipped:   {}".format(len(skipped)),
        "",
    ]
    for _, old_val, new_val in planned[:300]:
        lines.append("{}  ->  {}".format(old_val, new_val))
    if len(planned) > 300:
        lines.append("... and {} more".format(len(planned) - 300))
    if skipped:
        lines.append("")
        lines.append("Skipped (value too long):")
        for old_val, new_val, reason in skipped[:50]:
            lines.append("  {}  ->  {}  [{}]".format(old_val, new_val, reason))

    proceed = ui.uiUtils_show_text_report(
        "{} - Preview".format(title),
        "\n".join(lines),
        ok_text="Apply",
        cancel_text="Cancel",
        width=720,
        height=520,
    )
    if not proceed:
        return

    renamed, failed = apply_param_renames(doc, planned, param_name, transaction_title)
    result_lines = ["Updated: {}".format(len(renamed)), "Failed:  {}".format(len(failed))]
    if failed:
        result_lines += ["", "Failed (first 20):"]
        for old_val, new_val, error_text in failed[:20]:
            result_lines.append("  {}  ->  {}  ({})".format(old_val, new_val, error_text))

    ui.uiUtils_show_text_report(
        "{} - Results".format(title),
        "\n".join(result_lines),
        ok_text="Close",
        cancel_text=None,
        width=580,
        height=380,
    )


def run_selection(script_dir, lib_path):
    title = TOOL_TITLE_SELECTION
    inputs = show_dialog_selection(script_dir, lib_path)
    if not inputs:
        return

    param_name    = inputs["param_name"]
    find_text     = inputs["find"]
    replace_text  = inputs["replace"]
    prefix        = inputs["prefix"]
    suffix        = inputs["suffix"]
    ignore_groups = inputs["ignore_groups"]

    if not any([find_text, prefix, suffix]):
        ui.uiUtils_alert("Provide at least a Find text, Prefix, or Suffix value.", title=title)
        return

    elements = collect_elements_from_selection(doc, ignore_groups)
    if not elements:
        msg = "No editable instance elements found in the current selection."
        if ignore_groups:
            msg += "\n(Elements in groups are excluded — uncheck 'Ignore elements in groups' to include them.)"
        ui.uiUtils_alert(msg, title=title)
        return

    planned, skipped = plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix)
    if not planned:
        ui.uiUtils_alert("No parameter values matched the criteria.\nSkipped: {}".format(len(skipped)), title=title)
        return

    scope_lines = [
        "Parameter: {}".format(param_name),
        "Source:    Current Selection",
    ]
    _show_preview_and_apply(doc, planned, skipped, scope_lines, param_name, title, title)


def run_category(script_dir, lib_path):
    title = TOOL_TITLE_CATEGORY
    inputs = show_dialog_category(script_dir, lib_path)
    if not inputs:
        return

    cat_id        = inputs["cat_id"]
    cat_name      = inputs["cat_name"]
    param_name    = inputs["param_name"]
    find_text     = inputs["find"]
    replace_text  = inputs["replace"]
    prefix        = inputs["prefix"]
    suffix        = inputs["suffix"]
    ignore_groups = inputs["ignore_groups"]

    if not any([find_text, prefix, suffix]):
        ui.uiUtils_alert("Provide at least a Find text, Prefix, or Suffix value.", title=title)
        return

    elements = collect_elements_by_category(doc, cat_id, ignore_groups)
    if not elements:
        msg = "No editable instance elements found in category '{}'.".format(cat_name)
        if ignore_groups:
            msg += "\n(Elements in groups are excluded — uncheck 'Ignore elements in groups' to include them.)"
        ui.uiUtils_alert(msg, title=title)
        return

    planned, skipped = plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix)
    if not planned:
        ui.uiUtils_alert("No parameter values matched the criteria.\nSkipped: {}".format(len(skipped)), title=title)
        return

    scope_lines = [
        "Category:  {}".format(cat_name),
        "Parameter: {}".format(param_name),
    ]
    _show_preview_and_apply(doc, planned, skipped, scope_lines, param_name, title, title)


def run_with_error_dialog_selection(script_dir, lib_path):
    try:
        run_selection(script_dir, lib_path)
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="{} - Error".format(TOOL_TITLE_SELECTION))


def run_with_error_dialog_category(script_dir, lib_path):
    try:
        run_category(script_dir, lib_path)
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="{} - Error".format(TOOL_TITLE_CATEGORY))
