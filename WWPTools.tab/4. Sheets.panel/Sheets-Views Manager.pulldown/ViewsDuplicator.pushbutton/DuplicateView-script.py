import clr
import os
import sys
import traceback

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit import DB
from System.IO import File
from System.Windows import RoutedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

import WWP_uiUtils as ui

TITLE = "Views Duplicator"
PARAM_VIEW_SUBCATEGORY = "View Subcategory"




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def _clean_name(name):
    if not name:
        return ""
    return name.replace("{", "").replace("}", "")


def _collect_selected_views(uidoc, doc):
    views = []
    skipped = []
    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        if isinstance(element, DB.View) and not element.IsTemplate:
            views.append(element)
        else:
            skipped.append(element)
    return views, skipped


def _build_existing_names(doc):
    names = set()
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            names.add(view.Name)
        except Exception:
            pass
    return names


def _ensure_unique_name(existing, base_name):
    if base_name not in existing:
        existing.add(base_name)
        return base_name
    index = 2
    while True:
        candidate = "{} ({})".format(base_name, index)
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        index += 1


def _build_new_name(original, find_text, replace_text, prefix, suffix):
    name = original
    if find_text:
        name = name.replace(find_text, replace_text)
    if prefix:
        name = prefix + name
    if suffix:
        name = name + suffix
    return name


def _param_to_string(param):
    if param is None:
        return ""
    try:
        if param.StorageType == DB.StorageType.String:
            return param.AsString() or ""
        value = param.AsValueString()
        if value is not None:
            return value
        if param.StorageType == DB.StorageType.Integer:
            return str(param.AsInteger())
        if param.StorageType == DB.StorageType.Double:
            return str(param.AsDouble())
        if param.StorageType == DB.StorageType.ElementId:
            return str(_elem_id_int(param.AsElementId()))
    except Exception:
        return ""
    return ""


def _set_view_subcategory(view, suffix, errors):
    if not suffix:
        return
    param = view.LookupParameter(PARAM_VIEW_SUBCATEGORY)
    if param is None:
        errors.append("Missing parameter '{}' on view '{}'.".format(PARAM_VIEW_SUBCATEGORY, view.Name))
        return
    if param.IsReadOnly:
        errors.append("Parameter '{}' is read-only on view '{}'.".format(PARAM_VIEW_SUBCATEGORY, view.Name))
        return
    if param.StorageType != DB.StorageType.String:
        errors.append("Parameter '{}' is not a text parameter on view '{}'.".format(PARAM_VIEW_SUBCATEGORY, view.Name))
        return
    current = _param_to_string(param)
    try:
        param.Set("{}{}".format(current, suffix))
    except Exception as exc:
        errors.append("Failed to set '{}' on view '{}': {}".format(PARAM_VIEW_SUBCATEGORY, view.Name, exc))


def _duplicate_option_from_value(value):
    mapping = {
        "Duplicate":     DB.ViewDuplicateOption.Duplicate,
        "AsDependent":   DB.ViewDuplicateOption.AsDependent,
        "WithDetailing": DB.ViewDuplicateOption.WithDetailing,
    }
    return mapping.get(value, DB.ViewDuplicateOption.WithDetailing)


# ---------------------------------------------------------------------------
# WPF dialog
# ---------------------------------------------------------------------------

def _ensure_theme():
    try:
        ver = int(str(__revit__.Application.VersionNumber))
    except Exception:
        ver = None
    dll_name = (
        "WWPTools.WpfUI.net8.0-windows.dll"
        if ver and ver >= 2025
        else "WWPTools.WpfUI.net48.dll"
    )
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


def show_dialog(view_count):
    _ensure_theme()

    uidoc = __revit__.ActiveUIDocument
    xaml_path = os.path.join(script_dir, "DuplicateViewWindow.xaml")
    window = XamlReader.Parse(File.ReadAllText(xaml_path))

    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle if uidoc else 0
    except Exception:
        pass

    txt_source     = window.FindName("TxtSource")
    rb_duplicate   = window.FindName("RbDuplicate")
    rb_dependent   = window.FindName("RbDependent")
    rb_detailing   = window.FindName("RbWithDetailing")
    txt_find       = window.FindName("TxtFind")
    txt_replace    = window.FindName("TxtReplace")
    txt_prefix     = window.FindName("TxtPrefix")
    txt_suffix     = window.FindName("TxtSuffix")
    btn_cancel     = window.FindName("BtnCancel")
    btn_duplicate  = window.FindName("BtnDuplicate")

    txt_source.Text = "Source: {} view(s) selected".format(view_count)

    result = [None]

    def _on_duplicate(sender, args):
        if rb_duplicate.IsChecked:
            opt = "Duplicate"
        elif rb_dependent.IsChecked:
            opt = "AsDependent"
        else:
            opt = "WithDetailing"
        result[0] = {
            "duplicate_option": opt,
            "find":    txt_find.Text or "",
            "replace": txt_replace.Text or "",
            "prefix":  txt_prefix.Text or "",
            "suffix":  txt_suffix.Text or "",
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    btn_duplicate.Click += RoutedEventHandler(_on_duplicate)
    btn_cancel.Click    += RoutedEventHandler(_on_cancel)

    if window.ShowDialog() != True:
        return None
    return result[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        return
    doc = uidoc.Document

    views, skipped = _collect_selected_views(uidoc, doc)
    if not views:
        ui.uiUtils_alert("Select at least one view.", title=TITLE)
        return

    form_result = show_dialog(len(views))
    if not form_result:
        return

    find_text        = form_result.get("find", "")
    replace_text     = form_result.get("replace", "")
    prefix           = form_result.get("prefix", "")
    suffix           = form_result.get("suffix", "")
    duplicate_option = _duplicate_option_from_value(form_result.get("duplicate_option"))

    existing_names = _build_existing_names(doc)
    errors = []
    new_ids = []

    transaction = DB.Transaction(doc, "Duplicate Views")
    transaction.Start()
    try:
        for view in views:
            try:
                new_id = view.Duplicate(duplicate_option)
                new_view = doc.GetElement(new_id)
            except Exception as exc:
                errors.append("Failed to duplicate '{}': {}".format(view.Name, exc))
                continue

            clean_name = _clean_name(view.Name)
            new_name = _build_new_name(clean_name, find_text, replace_text, prefix, suffix)
            target_name = _ensure_unique_name(existing_names, new_name)

            try:
                new_view.Name = target_name
            except Exception as exc:
                errors.append("Failed to rename view '{}': {}".format(view.Name, exc))

            try:
                new_view.ViewTemplateId = DB.ElementId.InvalidElementId
            except Exception as exc:
                errors.append("Failed to remove template from '{}': {}".format(target_name, exc))

            _set_view_subcategory(new_view, suffix, errors)
            new_ids.append(new_id)
    finally:
        if transaction.HasStarted():
            transaction.Commit()

    # Select the duplicated views instead of the originals
    if new_ids:
        try:
            from System.Collections.Generic import List
            id_list = List[DB.ElementId](new_ids)
            uidoc.Selection.SetElementIds(id_list)
        except Exception:
            pass

    if skipped:
        errors.append("Skipped {} non-view selections.".format(len(skipped)))

    if errors:
        ui.uiUtils_alert("\n".join(errors), title=TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=TITLE)
