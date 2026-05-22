import os
import re
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import DB
from System.IO import File
from System.Windows import Visibility
from System.Windows.Controls import ComboBoxItem, SelectionChangedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

import WWP_uiUtils as ui


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document if uidoc else None




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.IntegerValue)  # Revit 2023-

def _mode_config(mode):
    if mode == "selection":
        return {
            "title": "Super Renamer(by Selections)",
            "header": "Super Renamer(by Selections)",
            "subtitle": "Find and replace names for the current Revit selection.",
            "selector_label": "Selection:",
            "options": [("Current Selection", "Current Selection")],
            "selector_enabled": False,
            "transaction_title": "Super Renamer(by Selections)",
        }
    return {
        "title": "Super Renamer(by Category)",
        "header": "Super Renamer(by Category)",
        "subtitle": "Find and replace names across your project.",
        "selector_label": "Category:",
        "options": [
            ("Materials", "Materials"),
            ("Views", "Views"),
            ("View Templates", "View Templates"),
            ("Sheets", "Sheets"),
            ("Levels", "Levels"),
            ("Grids", "Grids"),
            ("Rooms", "Rooms"),
            ("Spaces", "Spaces"),
            ("Areas", "Areas"),
            ("View Filters", "View Filters"),
            ("Phases", "Phases"),
            ("Types (Selection)", "Types (Selection)"),
        ],
        "selector_enabled": True,
        "transaction_title": "Super Renamer(by Category)",
    }


TARGET_OPTIONS = [
    ("Element names", "element_names"),
    ("Family names", "family_names"),
    ("Type names", "type_names"),
    ("Instance parameter values", "instance_params"),
]

ELEMENT_SCOPE_OPTIONS = [
    ("Materials", "Materials"),
    ("Views", "Views"),
    ("View Templates", "View Templates"),
    ("Sheets", "Sheets"),
    ("Levels", "Levels"),
    ("Grids", "Grids"),
    ("Rooms", "Rooms"),
    ("Spaces", "Spaces"),
    ("Areas", "Areas"),
    ("View Filters", "View Filters"),
    ("Phases", "Phases"),
    ("Types (Selection)", "Types (Selection)"),
]


def _source_label(scope_key):
    if scope_key in ("Types (Selection)", "Current Selection"):
        return "Source: Current selection"
    return "Source: All elements in document"


def _get_name(element):
    try:
        return element.Name or ""
    except Exception:
        return ""


def _set_name(element, name):
    element.Name = name


def _is_in_group(element):
    try:
        gid = element.GroupId
        return gid is not None and gid != DB.ElementId.InvalidElementId
    except Exception:
        return False


def _build_new_name(current, find_text, replace_text, prefix, suffix):
    new_name = current
    if find_text:
        new_name = re.sub(re.escape(find_text), replace_text, new_name, flags=re.IGNORECASE)
    if prefix:
        new_name = "{}{}".format(prefix, new_name)
    if suffix:
        new_name = "{}{}".format(new_name, suffix)
    return new_name


def _add_unique(targets, seen_ids, element):
    if element is None:
        return
    try:
        element_id = _elem_id_int(element.Id)
    except Exception:
        return
    if element_id in seen_ids:
        return
    if not _get_name(element):
        return
    seen_ids.add(element_id)
    targets.append(element)


def _get_family_from_element_type(element_type):
    if element_type is None:
        return None
    try:
        family = element_type.Family
        if family is not None:
            return family
    except Exception:
        pass
    try:
        family_name = element_type.FamilyName
        if not family_name:
            return None
        families = DB.FilteredElementCollector(doc).OfClass(DB.Family).ToElements()
        for family in families:
            if _get_name(family) == family_name:
                return family
    except Exception:
        pass
    return None


def _get_type_from_instance(current_doc, element):
    try:
        type_id = element.GetTypeId()
        if type_id and type_id != DB.ElementId.InvalidElementId:
            return current_doc.GetElement(type_id)
    except Exception:
        pass
    return None


def _add_type_for_instance(targets, seen_ids, current_doc, element):
    _add_unique(targets, seen_ids, _get_type_from_instance(current_doc, element))


def _add_family_for_instance(targets, seen_ids, current_doc, element):
    _add_unique(targets, seen_ids, _get_family_from_element_type(_get_type_from_instance(current_doc, element)))


def _get_selected_targets(current_doc):
    try:
        current_uidoc = __revit__.ActiveUIDocument
    except Exception:
        return []
    if current_uidoc is None or current_doc is None:
        return []
    try:
        selected_ids = list(current_uidoc.Selection.GetElementIds())
    except Exception:
        return []

    targets = []
    seen_ids = set()
    direct_types = (
        DB.Material,
        DB.View,
        DB.ViewSheet,
        DB.Level,
        DB.Grid,
        DB.FilterElement,
        DB.Family,
    )

    for element_id in selected_ids:
        element = current_doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, DB.ElementType):
            _add_unique(targets, seen_ids, element)
            continue
        if isinstance(element, direct_types):
            _add_unique(targets, seen_ids, element)
            continue
        try:
            category_id = _elem_id_int(element.Category.Id) if element.Category else None
        except Exception:
            category_id = None
        if category_id in (
            int(DB.BuiltInCategory.OST_Rooms),
            int(DB.BuiltInCategory.OST_MEPSpaces),
            int(DB.BuiltInCategory.OST_Areas),
        ):
            _add_unique(targets, seen_ids, element)
            continue
        try:
            type_id = element.GetTypeId()
            if type_id and type_id != DB.ElementId.InvalidElementId:
                _add_unique(targets, seen_ids, current_doc.GetElement(type_id))
        except Exception:
            pass
    return targets


def _get_selected_instances(current_doc, ignore_groups):
    try:
        current_uidoc = __revit__.ActiveUIDocument
        selected_ids = list(current_uidoc.Selection.GetElementIds())
    except Exception:
        return []
    elements = []
    seen_ids = set()
    for element_id in selected_ids:
        element = current_doc.GetElement(element_id)
        if element is None or isinstance(element, DB.ElementType):
            continue
        if ignore_groups and _is_in_group(element):
            continue
        try:
            element_key = _elem_id_int(element.Id)
        except Exception:
            continue
        if element_key in seen_ids:
            continue
        seen_ids.add(element_key)
        elements.append(element)
    return elements


def _get_selected_types(current_doc):
    try:
        current_uidoc = __revit__.ActiveUIDocument
        selected_ids = list(current_uidoc.Selection.GetElementIds())
    except Exception:
        return []
    targets = []
    seen_ids = set()
    for element_id in selected_ids:
        element = current_doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, DB.ElementType):
            _add_unique(targets, seen_ids, element)
        else:
            _add_type_for_instance(targets, seen_ids, current_doc, element)
    return targets


def _get_selected_families(current_doc):
    try:
        current_uidoc = __revit__.ActiveUIDocument
        selected_ids = list(current_uidoc.Selection.GetElementIds())
    except Exception:
        return []
    targets = []
    seen_ids = set()
    for element_id in selected_ids:
        element = current_doc.GetElement(element_id)
        if element is None:
            continue
        if isinstance(element, DB.Family):
            _add_unique(targets, seen_ids, element)
        elif isinstance(element, DB.ElementType):
            _add_unique(targets, seen_ids, _get_family_from_element_type(element))
        else:
            _add_family_for_instance(targets, seen_ids, current_doc, element)
    return targets


def _get_category_options(current_doc):
    cats = {}
    try:
        all_elems = (
            DB.FilteredElementCollector(current_doc)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for element in all_elems:
            try:
                cat = element.Category
                if cat is not None and cat.CategoryType == DB.CategoryType.Model:
                    cats[cat.Name] = cat.Id
            except Exception:
                pass
    except Exception:
        pass
    return sorted(cats.items())


def _collect_instances_by_category(current_doc, cat_id, ignore_groups):
    try:
        elements = list(
            DB.FilteredElementCollector(current_doc)
            .OfCategoryId(cat_id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []
    if ignore_groups:
        elements = [e for e in elements if not _is_in_group(e)]
    return elements


def _collect_types_by_category(current_doc, cat_id):
    targets = []
    seen_ids = set()
    try:
        for element_type in (
            DB.FilteredElementCollector(current_doc)
            .OfCategoryId(cat_id)
            .WhereElementIsElementType()
            .ToElements()
        ):
            _add_unique(targets, seen_ids, element_type)
    except Exception:
        pass
    for element in _collect_instances_by_category(current_doc, cat_id, ignore_groups=False):
        _add_type_for_instance(targets, seen_ids, current_doc, element)
    return targets


def _collect_families_by_category(current_doc, cat_id):
    targets = []
    seen_ids = set()
    for element_type in _collect_types_by_category(current_doc, cat_id):
        _add_unique(targets, seen_ids, _get_family_from_element_type(element_type))
    return targets


def _get_string_parameter_names(elements):
    names = set()
    for element in elements:
        try:
            for param in element.Parameters:
                try:
                    if param.StorageType == DB.StorageType.String and not param.IsReadOnly:
                        names.add(param.Definition.Name)
                except Exception:
                    pass
        except Exception:
            pass
    return sorted(names)


def _get_param_value(element, param_name):
    param = element.LookupParameter(param_name)
    if param is None or param.StorageType != DB.StorageType.String or param.IsReadOnly:
        return None
    value = param.AsString()
    return value if value is not None else ""


def _set_param_value(element, param_name, new_value):
    param = element.LookupParameter(param_name)
    if param is None or param.StorageType != DB.StorageType.String or param.IsReadOnly:
        raise Exception("Parameter '{}' not found or read-only".format(param_name))
    param.Set(new_value)


def collect_elements(current_doc, scope_key):
    fec = DB.FilteredElementCollector
    if current_doc is None:
        return []
    if scope_key in ("Current Selection", "Types (Selection)"):
        return _get_selected_targets(current_doc)
    if scope_key == "Materials":
        return list(fec(current_doc).OfClass(DB.Material).ToElements())
    if scope_key == "Views":
        views = []
        for v in fec(current_doc).OfClass(DB.View).ToElements():
            try:
                if not v.IsTemplate and v.ViewType not in (
                    DB.ViewType.Schedule, DB.ViewType.DrawingSheet, DB.ViewType.Internal
                ):
                    views.append(v)
            except Exception:
                pass
        return views
    if scope_key == "View Templates":
        return [v for v in fec(current_doc).OfClass(DB.View).ToElements() if v.IsTemplate]
    if scope_key == "Sheets":
        return list(fec(current_doc).OfClass(DB.ViewSheet).ToElements())
    if scope_key == "Levels":
        return list(fec(current_doc).OfClass(DB.Level).WhereElementIsNotElementType().ToElements())
    if scope_key == "Grids":
        return list(fec(current_doc).OfClass(DB.Grid).WhereElementIsNotElementType().ToElements())
    if scope_key == "Rooms":
        return [
            e for e in fec(current_doc).OfCategory(DB.BuiltInCategory.OST_Rooms)
            .WhereElementIsNotElementType()
            .ToElements()
            if e is not None and _get_name(e)
        ]
    if scope_key == "Spaces":
        return [
            e for e in fec(current_doc).OfCategory(DB.BuiltInCategory.OST_MEPSpaces)
            .WhereElementIsNotElementType()
            .ToElements()
            if e is not None
        ]
    if scope_key == "Areas":
        return [
            e for e in fec(current_doc).OfCategory(DB.BuiltInCategory.OST_Areas)
            .WhereElementIsNotElementType()
            .ToElements()
            if e is not None
        ]
    if scope_key == "View Filters":
        return list(fec(current_doc).OfClass(DB.FilterElement).ToElements())
    if scope_key == "Phases":
        try:
            return list(current_doc.Phases)
        except Exception:
            return []
    return []


def collect_target_elements(current_doc, target_key, scope_key, cat_id=None, ignore_groups=False):
    if current_doc is None:
        return []
    if target_key == "element_names":
        return collect_elements(current_doc, scope_key)
    if target_key == "type_names":
        if scope_key == "Current Selection":
            return _get_selected_types(current_doc)
        return _collect_types_by_category(current_doc, cat_id)
    if target_key == "family_names":
        if scope_key == "Current Selection":
            return _get_selected_families(current_doc)
        return _collect_families_by_category(current_doc, cat_id)
    if target_key == "instance_params":
        if scope_key == "Current Selection":
            return _get_selected_instances(current_doc, ignore_groups)
        return _collect_instances_by_category(current_doc, cat_id, ignore_groups)
    return []


def plan_renames(elements, find_text, replace_text, prefix, suffix):
    existing_lower = {_get_name(e).lower() for e in elements if _get_name(e)}
    planned = []
    skipped = []

    for element in elements:
        old_name = _get_name(element)
        new_name = _build_new_name(old_name, find_text, replace_text, prefix, suffix).strip()

        if new_name == old_name:
            continue
        if not new_name:
            skipped.append((old_name, new_name, "empty name"))
            continue
        if len(new_name) > 255:
            skipped.append((old_name, new_name, "name too long"))
            continue
        if new_name.lower() in existing_lower and new_name.lower() != old_name.lower():
            skipped.append((old_name, new_name, "name conflict"))
            continue

        planned.append((element, old_name, new_name))
        existing_lower.discard(old_name.lower())
        existing_lower.add(new_name.lower())

    return planned, skipped


def plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix):
    planned = []
    skipped = []
    for element in elements:
        old_value = _get_param_value(element, param_name)
        if old_value is None:
            continue
        new_value = _build_new_name(old_value, find_text, replace_text, prefix, suffix).strip()
        if new_value == old_value:
            continue
        if len(new_value) > 255:
            skipped.append((old_value, new_value, "value too long"))
            continue
        planned.append((element, old_value, new_value))
    return planned, skipped


def apply_renames(current_doc, planned, transaction_title, scope_name):
    renamed = []
    failed = []
    transaction = DB.Transaction(current_doc, "{}: {}".format(transaction_title, scope_name))
    try:
        transaction.Start()
        for element, old_name, new_name in planned:
            try:
                _set_name(element, new_name)
                renamed.append((old_name, new_name))
            except Exception as ex:
                failed.append((old_name, new_name, str(ex)))
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
        except Exception:
            pass
        return [], failed + [("<transaction>", "<commit>", str(ex))]
    return renamed, failed


def apply_param_renames(current_doc, planned, param_name, transaction_title, scope_name):
    renamed = []
    failed = []
    transaction = DB.Transaction(current_doc, "{}: {} - {}".format(transaction_title, scope_name, param_name))
    try:
        transaction.Start()
        for element, old_value, new_value in planned:
            try:
                _set_param_value(element, param_name, new_value)
                renamed.append((old_value, new_value))
            except Exception as ex:
                failed.append((old_value, new_value, str(ex)))
        transaction.Commit()
    except Exception as ex:
        try:
            transaction.RollBack()
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


def show_dialog(script_dir, lib_path, mode):
    config = _mode_config(mode)
    _ensure_theme(lib_path)

    xaml_path = os.path.join(script_dir, "SuperRenamer.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing XAML file: {}".format(xaml_path))

    window = XamlReader.Parse(File.ReadAllText(xaml_path))
    _set_owner(window)

    lbl_selector = window.FindName("LblSelector")
    lbl_target = window.FindName("LblTarget")
    txt_header = window.FindName("TxtHeader")
    txt_subtitle = window.FindName("TxtSubtitle")
    cmb_target = window.FindName("CmbTarget")
    cmb_category = window.FindName("CmbCategory")
    row_parameter = window.FindName("RowParameter")
    cmb_parameter = window.FindName("CmbParameter")
    chk_ignore_groups = window.FindName("ChkIgnoreGroups")
    row_ignore_groups = window.FindName("RowIgnoreGroups") or chk_ignore_groups
    txt_source = window.FindName("TxtSource")
    txt_find = window.FindName("TxtFind")
    txt_replace = window.FindName("TxtReplace")
    txt_prefix = window.FindName("TxtPrefix")
    txt_suffix = window.FindName("TxtSuffix")
    btn_cancel = window.FindName("BtnCancel")
    btn_apply = window.FindName("BtnApply")

    window.Title = config["title"]
    txt_header.Text = config["header"]
    txt_subtitle.Text = config["subtitle"]
    lbl_target.Content = "Rename:"
    lbl_selector.Content = config["selector_label"]

    target_keys = []
    for display_name, target_key in TARGET_OPTIONS:
        item = ComboBoxItem()
        item.Content = display_name
        cmb_target.Items.Add(item)
        target_keys.append(target_key)
    cmb_target.SelectedIndex = 0

    category_keys = []
    category_ids = []
    category_names = []
    param_names = []

    def _target_key():
        idx = cmb_target.SelectedIndex
        if idx < 0 or idx >= len(target_keys):
            return "element_names"
        return target_keys[idx]

    def _target_display():
        item = cmb_target.SelectedItem
        try:
            return str(item.Content or "")
        except Exception:
            return str(item or "")

    def _selected_key():
        idx = cmb_category.SelectedIndex
        if idx < 0 or idx >= len(category_keys):
            return ""
        return category_keys[idx]

    def _selected_category_id():
        idx = cmb_category.SelectedIndex
        if idx < 0 or idx >= len(category_ids):
            return None
        return category_ids[idx]

    def _selected_display():
        item = cmb_category.SelectedItem
        try:
            return str(item.Content or "")
        except Exception:
            return str(item or "")

    def _populate_parameter_combo(elements):
        cmb_parameter.Items.Clear()
        del param_names[:]
        names = _get_string_parameter_names(elements)
        param_names.extend(names)
        if not names:
            item = ComboBoxItem()
            item.Content = "(no editable text parameters)"
            item.IsEnabled = False
            cmb_parameter.Items.Add(item)
            cmb_parameter.SelectedIndex = 0
            return
        for name in names:
            item = ComboBoxItem()
            item.Content = name
            cmb_parameter.Items.Add(item)
        cmb_parameter.SelectedIndex = 0

    def _refresh_parameter_combo():
        if _target_key() != "instance_params":
            cmb_parameter.Items.Clear()
            del param_names[:]
            return
        elements = collect_target_elements(
            doc,
            "instance_params",
            _selected_key(),
            _selected_category_id(),
            chk_ignore_groups.IsChecked == True,
        )
        _populate_parameter_combo(elements)

    def _refresh_category_combo():
        cmb_category.Items.Clear()
        del category_keys[:]
        del category_ids[:]
        del category_names[:]
        if mode == "selection":
            item = ComboBoxItem()
            item.Content = "Current Selection"
            cmb_category.Items.Add(item)
            category_keys.append("Current Selection")
            category_ids.append(None)
            category_names.append("Current Selection")
            cmb_category.IsEnabled = False
            cmb_category.SelectedIndex = 0
            return
        if _target_key() == "element_names":
            for display_name, scope_key in ELEMENT_SCOPE_OPTIONS:
                item = ComboBoxItem()
                item.Content = display_name
                cmb_category.Items.Add(item)
                category_keys.append(scope_key)
                category_ids.append(None)
                category_names.append(display_name)
        else:
            for cat_name, cat_id in _get_category_options(doc):
                item = ComboBoxItem()
                item.Content = cat_name
                cmb_category.Items.Add(item)
                category_keys.append(cat_name)
                category_ids.append(cat_id)
                category_names.append(cat_name)
        cmb_category.IsEnabled = config["selector_enabled"]
        if cmb_category.Items.Count > 0:
            cmb_category.SelectedIndex = 0

    def _refresh_target_controls():
        is_param = _target_key() == "instance_params"
        if row_parameter is not None:
            row_parameter.Visibility = Visibility.Visible if is_param else Visibility.Collapsed
        if row_ignore_groups is not None:
            row_ignore_groups.Visibility = Visibility.Visible if is_param else Visibility.Collapsed
        _refresh_category_combo()
        txt_source.Text = _source_label(_selected_key())
        _refresh_parameter_combo()

    _refresh_target_controls()
    cmb_category.IsEnabled = config["selector_enabled"]

    result = [None]

    def _on_selector_changed(sender, args):
        txt_source.Text = _source_label(_selected_key())
        _refresh_parameter_combo()

    def _on_target_changed(sender, args):
        _refresh_target_controls()

    def _on_ignore_groups_changed(sender, args):
        _refresh_parameter_combo()

    def _on_apply(sender, args):
        param_name = ""
        if _target_key() == "instance_params":
            param_idx = cmb_parameter.SelectedIndex
            if param_idx < 0 or param_idx >= len(param_names):
                ui.uiUtils_alert("No editable text parameters available for the current scope.", title=config["title"])
                return
            param_name = param_names[param_idx]
        result[0] = {
            "target_key": _target_key(),
            "target_display": _target_display(),
            "scope_key": _selected_key(),
            "scope_display": _selected_display(),
            "cat_id": _selected_category_id(),
            "param_name": param_name,
            "find": txt_find.Text or "",
            "replace": txt_replace.Text or "",
            "prefix": txt_prefix.Text or "",
            "suffix": txt_suffix.Text or "",
            "ignore_groups": chk_ignore_groups.IsChecked == True,
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    cmb_target.SelectionChanged += SelectionChangedEventHandler(_on_target_changed)
    cmb_category.SelectionChanged += SelectionChangedEventHandler(_on_selector_changed)
    chk_ignore_groups.Checked += _on_ignore_groups_changed
    chk_ignore_groups.Unchecked += _on_ignore_groups_changed
    btn_apply.Click += _on_apply
    btn_cancel.Click += _on_cancel

    if window.ShowDialog() != True:
        return None
    return result[0]


def run(script_dir, lib_path, mode):
    config = _mode_config(mode)
    inputs = show_dialog(script_dir, lib_path, mode)
    if not inputs:
        return

    scope_key = inputs["scope_key"]
    scope_display = inputs["scope_display"]
    target_key = inputs["target_key"]
    target_display = inputs["target_display"]
    cat_id = inputs["cat_id"]
    param_name = inputs["param_name"]
    find_text = inputs["find"]
    replace_text = inputs["replace"]
    prefix = inputs["prefix"]
    suffix = inputs["suffix"]
    ignore_groups = inputs["ignore_groups"]

    if not any([find_text, prefix, suffix]):
        ui.uiUtils_alert("Provide at least a Find text, Prefix, or Suffix value.", title=config["title"])
        return

    elements = collect_target_elements(doc, target_key, scope_key, cat_id, ignore_groups)
    if not elements:
        if scope_key in ("Current Selection", "Types (Selection)"):
            msg = "No renameable items found in the current selection."
        else:
            msg = "No {} found for {}.".format(target_display.lower(), scope_display.lower())
        ui.uiUtils_alert(msg, title=config["title"])
        return

    if target_key == "instance_params":
        planned, skipped = plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix)
    else:
        planned, skipped = plan_renames(elements, find_text, replace_text, prefix, suffix)
    if not planned:
        ui.uiUtils_alert("No values matched the criteria.\nSkipped: {}".format(len(skipped)), title=config["title"])
        return

    lines = [
        "Scope:     {}".format(scope_display),
        "Target:    {}".format(target_display),
    ]
    if target_key == "instance_params":
        lines.append("Parameter: {}".format(param_name))
    lines += [
        "To update: {}".format(len(planned)),
        "Skipped:   {}".format(len(skipped)),
        "",
    ]
    for _, old_name, new_name in planned[:300]:
        lines.append("{}  ->  {}".format(old_name, new_name))
    if len(planned) > 300:
        lines.append("... and {} more".format(len(planned) - 300))
    if skipped:
        lines.append("")
        lines.append("Skipped (conflicts / invalid):")
        for old_name, new_name, reason in skipped[:50]:
            lines.append("  {}  ->  {}  [{}]".format(old_name, new_name, reason))

    proceed = ui.uiUtils_show_text_report(
        "{} - Preview".format(config["title"]),
        "\n".join(lines),
        ok_text="Apply",
        cancel_text="Cancel",
        width=720,
        height=520,
    )
    if not proceed:
        return

    if target_key == "instance_params":
        renamed, failed = apply_param_renames(doc, planned, param_name, config["transaction_title"], scope_display)
    else:
        renamed, failed = apply_renames(doc, planned, config["transaction_title"], scope_display)

    result_lines = ["Updated: {}".format(len(renamed)), "Failed:  {}".format(len(failed))]
    if failed:
        result_lines += ["", "Failed (first 20):"]
        for old_name, new_name, error_text in failed[:20]:
            result_lines.append("  {}  ->  {}  ({})".format(old_name, new_name, error_text))

    ui.uiUtils_show_text_report(
        "{} - Results".format(config["title"]),
        "\n".join(result_lines),
        ok_text="Close",
        cancel_text=None,
        width=580,
        height=380,
    )


def run_with_error_dialog(script_dir, lib_path, mode):
    config = _mode_config(mode)
    try:
        run(script_dir, lib_path, mode)
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="{} - Error".format(config["title"]))
