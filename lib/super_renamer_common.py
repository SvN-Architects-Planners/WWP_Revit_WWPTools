import os
import re
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import DB
from System.IO import File
from System.Windows import RoutedEventHandler
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
    txt_header = window.FindName("TxtHeader")
    txt_subtitle = window.FindName("TxtSubtitle")
    cmb_category = window.FindName("CmbCategory")
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
    lbl_selector.Content = config["selector_label"]
    cmb_category.Items.Clear()
    key_by_index = []
    for display_name, scope_key in config["options"]:
        item = ComboBoxItem()
        item.Content = display_name
        cmb_category.Items.Add(item)
        key_by_index.append(scope_key)
    cmb_category.IsEnabled = config["selector_enabled"]
    cmb_category.SelectedIndex = 0
    txt_source.Text = _source_label(key_by_index[0])

    result = [None]

    def _selected_key():
        idx = cmb_category.SelectedIndex
        if idx < 0 or idx >= len(key_by_index):
            return ""
        return key_by_index[idx]

    def _selected_display():
        item = cmb_category.SelectedItem
        try:
            return str(item.Content or "")
        except Exception:
            return str(item or "")

    def _on_selector_changed(sender, args):
        txt_source.Text = _source_label(_selected_key())

    def _on_apply(sender, args):
        result[0] = {
            "scope_key": _selected_key(),
            "scope_display": _selected_display(),
            "find": txt_find.Text or "",
            "replace": txt_replace.Text or "",
            "prefix": txt_prefix.Text or "",
            "suffix": txt_suffix.Text or "",
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    cmb_category.SelectionChanged += SelectionChangedEventHandler(_on_selector_changed)
    btn_apply.Click += RoutedEventHandler(_on_apply)
    btn_cancel.Click += RoutedEventHandler(_on_cancel)

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
    find_text = inputs["find"]
    replace_text = inputs["replace"]
    prefix = inputs["prefix"]
    suffix = inputs["suffix"]

    if not any([find_text, prefix, suffix]):
        ui.uiUtils_alert("Provide at least a Find text, Prefix, or Suffix value.", title=config["title"])
        return

    elements = collect_elements(doc, scope_key)
    if not elements:
        if scope_key in ("Current Selection", "Types (Selection)"):
            msg = "No renameable items found in the current selection."
        else:
            msg = "No {} found in the document.".format(scope_display.lower())
        ui.uiUtils_alert(msg, title=config["title"])
        return

    planned, skipped = plan_renames(elements, find_text, replace_text, prefix, suffix)
    if not planned:
        ui.uiUtils_alert("No elements matched the criteria.\nSkipped: {}".format(len(skipped)), title=config["title"])
        return

    lines = [
        "Scope:     {}".format(scope_display),
        "To rename: {}".format(len(planned)),
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

    renamed, failed = apply_renames(doc, planned, config["transaction_title"], scope_display)

    result_lines = ["Renamed: {}".format(len(renamed)), "Failed:  {}".format(len(failed))]
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
