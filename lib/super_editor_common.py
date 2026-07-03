import os
import re
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import DB
from System.IO import File
from System.Windows import FontWeights, Thickness, Visibility
from System.Windows.Controls import ComboBoxItem, ListBoxItem, TextBlock
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Color, SolidColorBrush

import WWP_uiUtils as ui


uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document if uidoc else None


def _elem_id_int(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def _workset_id_int(workset_id):
    try:
        return int(workset_id.IntegerValue)
    except AttributeError:
        return int(workset_id.Value)


def _mode_config(mode):
    if mode == "selection":
        return {
            "title": "Super Editor(by Selections)",
            "header": "Super Editor(by Selections)",
            "subtitle": "Find and replace names for the current Revit selection.",
            "selector_label": "Selection:",
            "options": [("Current Selection", "Current Selection")],
            "selector_enabled": False,
            "transaction_title": "Super Editor(by Selections)",
        }
    return {
        "title": "Super Editor(by Category)",
        "header": "Super Editor(by Category)",
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
        "transaction_title": "Super Editor(by Category)",
    }


TARGET_OPTIONS = [
    ("Element names  (Views, Sheets, Rooms...)", "element_names"),
    ("Type names", "type_names"),
    ("Instance parameter values", "instance_params"),
    ("Type parameter values", "type_params"),
]

_TARGET_TOOLTIPS = {
    "element_names":   "Rename views, sheets, levels, grids, rooms, materials, etc. -- pick the category below.",
    "type_names":      "Rename a family type (e.g. '900 x 2100mm'). Tick 'Also rename family name' below to rename the parent family with the same transformation.",
    "instance_params": "Find and replace a text parameter value on placed instances.",
    "type_params":     "Find and replace a parameter value on element types.",
    "workset":         "Move placed instances to a different user-created workset.",
}

ELEMENT_SCOPE_OPTIONS = [
    ("Areas", "Areas"),
    ("Grids", "Grids"),
    ("Levels", "Levels"),
    ("Materials", "Materials"),
    ("Phases", "Phases"),
    ("Rooms", "Rooms"),
    ("Sheets", "Sheets"),
    ("Spaces", "Spaces"),
    ("Types (Selection)", "Types (Selection)"),
    ("View Filters", "View Filters"),
    ("View Templates", "View Templates"),
    ("Views", "Views"),
]


def _source_label(scope_key):
    if scope_key in ("Types (Selection)", "Current Selection"):
        return "Source: Current selection"
    return "Source: All elements in document"


def _get_name(element):
    # DB.Element.Name getter is write-only (CanRead=False) for FamilySymbol in some
    # IronPython 3 / Revit builds. Fall back to the built-in parameter for types.
    try:
        name = element.Name
        if name is not None:
            return str(name)
    except Exception:
        pass
    try:
        p = element.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            v = p.AsString()
            if v:
                return v
    except Exception:
        pass
    try:
        p = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None:
            v = p.AsString()
            if v:
                return v
    except Exception:
        pass
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
        _model = DB.CategoryType.Model
        _annot = DB.CategoryType.Annotation
        for element in (DB.FilteredElementCollector(current_doc)
                        .WhereElementIsNotElementType().ToElements()):
            try:
                cat = element.Category
                ct = cat.CategoryType if cat is not None else None
                if (ct == _model or ct == _annot) and cat.Name and not cat.Name.startswith("<"):
                    cats[cat.Name] = cat.Id
            except Exception:
                pass
        for element in (DB.FilteredElementCollector(current_doc)
                        .WhereElementIsElementType().ToElements()):
            try:
                cat = element.Category
                ct = cat.CategoryType if cat is not None else None
                if (ct == _model or ct == _annot) and cat.Name and not cat.Name.startswith("<"):
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


def _collect_types_by_category(current_doc, cat_id, cat_name=None):
    targets = []
    seen_ids = set()
    # Method 1: standard OfCategoryId filter
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
    # Method 2: FamilySymbol scan with integer ID comparison
    if not targets:
        try:
            cat_int = _elem_id_int(cat_id)
            for element_type in (
                DB.FilteredElementCollector(current_doc)
                .OfClass(DB.FamilySymbol)
                .ToElements()
            ):
                try:
                    if (element_type.Category is not None and
                            _elem_id_int(element_type.Category.Id) == cat_int):
                        _add_unique(targets, seen_ids, element_type)
                except Exception:
                    pass
        except Exception:
            pass
    # Method 3: scan DB.Family by category name string -- most reliable for loadable families
    if not targets and cat_name:
        try:
            for family in (
                DB.FilteredElementCollector(current_doc)
                .OfClass(DB.Family)
                .ToElements()
            ):
                try:
                    if (family.Category is not None and
                            family.Category.Name == cat_name):
                        for type_id in family.GetFamilySymbolIds():
                            try:
                                sym = current_doc.GetElement(type_id)
                                if sym is not None:
                                    _add_unique(targets, seen_ids, sym)
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass
    # Method 4: via placed instances
    for element in _collect_instances_by_category(current_doc, cat_id, ignore_groups=False):
        _add_type_for_instance(targets, seen_ids, current_doc, element)
    return targets


def _collect_families_by_category(current_doc, cat_id):
    targets = []
    seen_ids = set()
    for element_type in _collect_types_by_category(current_doc, cat_id):
        _add_unique(targets, seen_ids, _get_family_from_element_type(element_type))
    return targets


def _get_user_worksets(current_doc):
    """Return [(name, WorksetId), ...] for user-created worksets, sorted by name."""
    worksets = []
    try:
        for ws in DB.FilteredWorksetCollector(current_doc).OfKind(DB.WorksetKind.UserWorkset):
            worksets.append((ws.Name, ws.Id))
    except Exception:
        return []
    worksets.sort(key=lambda item: item[0].lower())
    return worksets


def _get_workset_name(current_doc, workset_id):
    try:
        ws = current_doc.GetWorksetTable().GetWorkset(workset_id)
        return ws.Name if ws is not None else "(unknown workset)"
    except Exception:
        return "(unknown workset)"


def collect_instances_for_workset(current_doc, scope_key, cat_id, ignore_groups):
    """Collect placed instances eligible for workset reassignment -- every workshared
    element has a workset, so unlike collect_elements_with_param there's no param filter."""
    if scope_key == "Current Selection":
        return _get_selected_instances(current_doc, ignore_groups)
    if scope_key == "Views":
        return [v for v in DB.FilteredElementCollector(current_doc).OfClass(DB.View).ToElements()
                if not v.IsTemplate and v.ViewType not in (
                    DB.ViewType.Schedule, DB.ViewType.DrawingSheet, DB.ViewType.Internal)]
    if scope_key == "Sheets":
        return list(DB.FilteredElementCollector(current_doc).OfClass(DB.ViewSheet).ToElements())
    return _collect_instances_by_category(current_doc, cat_id, ignore_groups)


def plan_workset_changes(current_doc, elements, target_workset_id):
    """Plan workset reassignment. Skips elements already on the target workset and
    elements that don't support worksets at all (their WorksetId comes back invalid)."""
    planned = []
    skipped = []
    target_id_int = _workset_id_int(target_workset_id)
    target_name = _get_workset_name(current_doc, target_workset_id)
    for element in elements:
        try:
            current_ws_id = element.WorksetId
        except Exception:
            current_ws_id = None
        if current_ws_id is None or current_ws_id == DB.WorksetId.InvalidWorksetId:
            skipped.append((_get_name(element) or "(unnamed)", "", "no workset support"))
            continue
        if _workset_id_int(current_ws_id) == target_id_int:
            continue
        old_name = _get_workset_name(current_doc, current_ws_id)
        planned.append((element, old_name, target_name))
    return planned, skipped


def apply_workset_changes(current_doc, planned, target_workset_id, transaction_title, scope_name):
    renamed = []
    failed = []
    target_id_int = _workset_id_int(target_workset_id)
    transaction = DB.Transaction(current_doc, "{}: {} - Workset".format(transaction_title, scope_name))
    try:
        transaction.Start()
        for element, old_name, new_name in planned:
            try:
                param = element.get_Parameter(DB.BuiltInParameter.ELEM_PARTITION_PARAM)
                if param is None or param.IsReadOnly:
                    raise Exception("Workset parameter not found or read-only")
                param.Set(target_id_int)
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


def _get_type_param_value(element, param_name):
    """Read any writable parameter as a display string (all storage types except ElementId)."""
    param = element.LookupParameter(param_name)
    if param is None or param.IsReadOnly or param.StorageType == DB.StorageType.ElementId:
        return None
    if param.StorageType == DB.StorageType.String:
        val = param.AsString()
        return val if val is not None else ""
    val = param.AsValueString()
    return val if val is not None else ""


def _set_type_param_value(element, param_name, new_value):
    """Set any writable parameter from a string value (all storage types except ElementId)."""
    param = element.LookupParameter(param_name)
    if param is None or param.IsReadOnly:
        raise Exception("Parameter '{}' not found or read-only".format(param_name))
    if param.StorageType == DB.StorageType.String:
        param.Set(new_value)
    elif param.StorageType == DB.StorageType.Integer:
        val_lower = new_value.strip().lower()
        if val_lower in ("yes", "true", "1"):
            param.Set(1)
        elif val_lower in ("no", "false", "0"):
            param.Set(0)
        else:
            param.Set(int(new_value.strip()))
    elif param.StorageType == DB.StorageType.Double:
        param.SetValueString(new_value.strip())
    elif param.StorageType == DB.StorageType.ElementId:
        raise Exception("ElementId parameters cannot be set by value string")
    else:
        raise Exception("Unsupported storage type for parameter '{}'".format(param_name))


def _get_params_by_family(current_doc, scope_key, cat_id=None, target_key="instance_params"):
    """Returns (all_params_sorted, [(family_name, param_names_sorted)]) including both instance and type params."""
    all_params = set()
    family_params = {}  # family_name -> set of param names

    def _family_name_for_type(elem_type):
        if elem_type is None:
            return "(Unknown)"
        try:
            fn = elem_type.FamilyName
            return fn if fn else "(Unknown)"
        except Exception:
            return "(Unknown)"

    def _scan_element(element, family_name):
        if family_name not in family_params:
            family_params[family_name] = set()
        try:
            for param in element.Parameters:
                try:
                    if param.StorageType == DB.StorageType.String and not param.IsReadOnly:
                        pname = param.Definition.Name
                        all_params.add(pname)
                        family_params[family_name].add(pname)
                except Exception:
                    pass
        except Exception:
            pass

    def _scan_type_element(element, family_name):
        if family_name not in family_params:
            family_params[family_name] = set()
        try:
            for param in element.Parameters:
                try:
                    if (not param.IsReadOnly and
                            param.StorageType != DB.StorageType.ElementId):
                        pname = param.Definition.Name
                        all_params.add(pname)
                        family_params[family_name].add(pname)
                except Exception:
                    pass
        except Exception:
            pass

    if target_key == "type_params":
        if scope_key == "Current Selection":
            types = _get_selected_types(current_doc)
        else:
            types = []
            try:
                types = list(
                    DB.FilteredElementCollector(current_doc)
                    .OfCategoryId(cat_id)
                    .WhereElementIsElementType()
                    .ToElements()
                )
            except Exception:
                pass
        for elem_type in types:
            fname = _family_name_for_type(elem_type)
            _scan_type_element(elem_type, fname)
    elif scope_key == "Current Selection":  # instance_params with current selection
        try:
            selected_ids = list(__revit__.ActiveUIDocument.Selection.GetElementIds())
        except Exception:
            selected_ids = []
        seen_type_ids = set()
        for eid in selected_ids:
            elem = current_doc.GetElement(eid)
            if elem is None:
                continue
            if isinstance(elem, DB.ElementType):
                fname = _family_name_for_type(elem)
                _scan_element(elem, fname)
            else:
                elem_type = _get_type_from_instance(current_doc, elem)
                fname = _family_name_for_type(elem_type)
                _scan_element(elem, fname)
                if elem_type:
                    tid = _elem_id_int(elem_type.Id)
                    if tid not in seen_type_ids:
                        seen_type_ids.add(tid)
                        _scan_element(elem_type, fname)
    else:
        instances = _collect_instances_by_category(current_doc, cat_id, ignore_groups=False)
        seen_type_ids = set()
        for elem in instances:
            elem_type = _get_type_from_instance(current_doc, elem)
            fname = _family_name_for_type(elem_type)
            _scan_element(elem, fname)
            if elem_type:
                tid = _elem_id_int(elem_type.Id)
                if tid not in seen_type_ids:
                    seen_type_ids.add(tid)
                    _scan_element(elem_type, fname)
        # Also scan types with no placed instances
        try:
            for et in (DB.FilteredElementCollector(current_doc)
                       .OfCategoryId(cat_id)
                       .WhereElementIsElementType()
                       .ToElements()):
                tid = _elem_id_int(et.Id)
                if tid not in seen_type_ids:
                    seen_type_ids.add(tid)
                    fname = _family_name_for_type(et)
                    _scan_element(et, fname)
        except Exception:
            pass

    families_sorted = sorted(
        (fname, sorted(pnames))
        for fname, pnames in family_params.items()
        if pnames
    )
    return sorted(all_params), families_sorted


def _populate_param_list(lst, all_params, families):
    """Build grouped ListBox: 'All Families' header + flat list, then per-family sections."""
    lst.Items.Clear()

    muted_brush = SolidColorBrush(Color.FromRgb(0x71, 0x71, 0x7A))   # zinc-500

    def _add_header(text):
        tb = TextBlock()
        tb.Text = text.upper()
        tb.FontSize = 10
        tb.FontWeight = FontWeights.SemiBold
        tb.Foreground = muted_brush
        item = ListBoxItem()
        item.Content = tb
        item.IsEnabled = False
        item.IsHitTestVisible = False
        item.Padding = Thickness(8, 7, 8, 2)
        item.Tag = None
        lst.Items.Add(item)

    def _add_param(name):
        item = ListBoxItem()
        item.Content = name
        item.Tag = name
        item.Padding = Thickness(16, 4, 8, 4)
        lst.Items.Add(item)

    if not all_params:
        item = ListBoxItem()
        item.Content = "(no editable text parameters found)"
        item.IsEnabled = False
        item.Padding = Thickness(10, 6, 10, 6)
        lst.Items.Add(item)
        return

    multi_family = len(families) > 1
    if multi_family:
        _add_header("All Families - {} parameters".format(len(all_params)))
    for name in all_params:
        _add_param(name)

    if multi_family:
        for fname, pnames in families:
            _add_header(fname)
            for name in pnames:
                _add_param(name)


def collect_elements_with_param(current_doc, scope_key, cat_id, param_name, ignore_groups):
    """Collect instances + types that have the given writable string param."""
    candidates = []
    seen_ids = set()

    if scope_key == "Current Selection":
        candidates = _get_selected_instances(current_doc, ignore_groups)
        for t in _get_selected_types(current_doc):
            try:
                tid = _elem_id_int(t.Id)
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    candidates.append(t)
            except Exception:
                pass
    elif scope_key == "Views":
        candidates = [v for v in DB.FilteredElementCollector(current_doc).OfClass(DB.View).ToElements()
                      if not v.IsTemplate and v.ViewType not in (
                          DB.ViewType.Schedule, DB.ViewType.DrawingSheet, DB.ViewType.Internal)]
    elif scope_key == "Sheets":
        candidates = list(DB.FilteredElementCollector(current_doc).OfClass(DB.ViewSheet).ToElements())
    else:
        candidates = _collect_instances_by_category(current_doc, cat_id, ignore_groups)
        for t in _collect_types_by_category(current_doc, cat_id):
            try:
                tid = _elem_id_int(t.Id)
                if tid not in seen_ids:
                    seen_ids.add(tid)
                    candidates.append(t)
            except Exception:
                pass

    result = []
    final_seen = set()
    for elem in candidates:
        try:
            eid = _elem_id_int(elem.Id)
            if eid in final_seen:
                continue
            final_seen.add(eid)
        except Exception:
            continue
        if _get_param_value(elem, param_name) is not None:
            result.append(elem)
    return result


def collect_types_with_param(current_doc, scope_key, cat_id, param_name):
    """Collect ElementTypes that have the given writable param, including unplaced types."""
    if scope_key in ("Views", "Sheets"):
        return []
    if scope_key == "Current Selection":
        candidates = _get_selected_types(current_doc)
    else:
        candidates = _collect_types_by_category(current_doc, cat_id)
    result = []
    seen_ids = set()
    for elem in candidates:
        try:
            eid = _elem_id_int(elem.Id)
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
        except Exception:
            continue
        if _get_type_param_value(elem, param_name) is not None:
            result.append(elem)
    return result


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
        return _collect_types_by_category(current_doc, cat_id, cat_name=scope_key)
    if target_key == "family_names":
        if scope_key == "Current Selection":
            return _get_selected_families(current_doc)
        return _collect_families_by_category(current_doc, cat_id)
    if target_key == "instance_params":
        if scope_key == "Current Selection":
            return _get_selected_instances(current_doc, ignore_groups)
        return _collect_instances_by_category(current_doc, cat_id, ignore_groups)
    return []


def plan_renames(elements, find_text, replace_text, prefix, suffix, overwrite_value=None):
    existing_lower = {_get_name(e).lower() for e in elements if _get_name(e)}
    planned = []
    skipped = []

    for element in elements:
        old_name = _get_name(element)
        if overwrite_value is not None:
            new_name = overwrite_value.strip()
        else:
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


def plan_param_renames(elements, param_name, find_text, replace_text, prefix, suffix, overwrite_value=None):
    planned = []
    skipped = []
    for element in elements:
        old_value = _get_param_value(element, param_name)
        if old_value is None:
            continue
        if overwrite_value is not None:
            new_value = overwrite_value
        else:
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


def plan_type_param_renames(elements, param_name, find_text, replace_text, prefix, suffix, overwrite_value=None):
    """Plan renames for type parameters of any storage type (String, Integer, Double)."""
    planned = []
    skipped = []
    for element in elements:
        old_value = _get_type_param_value(element, param_name)
        if old_value is None:
            continue
        if overwrite_value is not None:
            new_value = overwrite_value
        else:
            new_value = _build_new_name(old_value, find_text, replace_text, prefix, suffix).strip()
        if new_value == old_value:
            continue
        if len(new_value) > 255:
            skipped.append((old_value, new_value, "value too long"))
            continue
        planned.append((element, old_value, new_value))
    return planned, skipped


def apply_type_param_renames(current_doc, planned, param_name, transaction_title, scope_name):
    """Apply type parameter renames supporting all storage types (String, Integer, Double)."""
    renamed = []
    failed = []
    transaction = DB.Transaction(current_doc, "{}: {} - {}".format(transaction_title, scope_name, param_name))
    try:
        transaction.Start()
        for element, old_value, new_value in planned:
            try:
                _set_type_param_value(element, param_name, new_value)
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

    xaml_path = os.path.join(script_dir, "SuperEditor.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing XAML file: {}".format(xaml_path))

    window = XamlReader.Parse(File.ReadAllText(xaml_path))
    _set_owner(window)

    # --- find controls ---
    lbl_selector      = window.FindName("LblSelector")
    lbl_target        = window.FindName("LblTarget")
    txt_header        = window.FindName("TxtHeader")
    txt_subtitle      = window.FindName("TxtSubtitle")
    cmb_target        = window.FindName("CmbTarget")
    cmb_category      = window.FindName("CmbCategory")
    pnl_parameters    = window.FindName("PnlParameters")
    lst_parameters    = window.FindName("LstParameters")
    txt_source        = window.FindName("TxtSource")
    rb_transform      = window.FindName("RbTransform")
    rb_overwrite      = window.FindName("RbOverwrite")
    pnl_transform     = window.FindName("PnlTransform")
    pnl_overwrite     = window.FindName("PnlOverwrite")
    txt_overwrite     = window.FindName("TxtOverwrite")
    pnl_mode_toggle   = window.FindName("PnlModeToggle")
    pnl_workset       = window.FindName("PnlWorkset")
    lst_worksets      = window.FindName("LstWorksets")
    txt_find          = window.FindName("TxtFind")
    txt_replace       = window.FindName("TxtReplace")
    txt_prefix        = window.FindName("TxtPrefix")
    txt_suffix        = window.FindName("TxtSuffix")
    chk_ignore_groups      = window.FindName("ChkIgnoreGroups")
    chk_also_rename_family = window.FindName("ChkAlsoRenameFamily")
    btn_cancel             = window.FindName("BtnCancel")
    btn_apply         = window.FindName("BtnApply")

    window.Title       = config["title"]
    txt_header.Text    = config["header"]
    txt_subtitle.Text  = config["subtitle"]
    lbl_target.Content = "Rename:"
    lbl_selector.Content = config["selector_label"]

    # --- state ---
    target_keys    = []
    category_keys  = []
    category_ids   = []
    category_names = []

    # --- helpers ---
    def _target_key():
        idx = cmb_target.SelectedIndex
        return target_keys[idx] if 0 <= idx < len(target_keys) else "element_names"

    def _target_display():
        item = cmb_target.SelectedItem
        try:
            return str(item.Content or "")
        except Exception:
            return str(item or "")

    def _selected_key():
        idx = cmb_category.SelectedIndex
        return category_keys[idx] if 0 <= idx < len(category_keys) else ""

    def _selected_category_id():
        idx = cmb_category.SelectedIndex
        return category_ids[idx] if 0 <= idx < len(category_ids) else None

    def _selected_display():
        item = cmb_category.SelectedItem
        try:
            return str(item.Content or "")
        except Exception:
            return str(item or "")

    def _is_param_mode():
        return _target_key() in ("instance_params", "type_params")

    def _reset_param_panel():
        if pnl_parameters is not None:
            pnl_parameters.Visibility = Visibility.Collapsed
        if lst_parameters is not None:
            lst_parameters.Items.Clear()

    # --- populate target combo ---
    target_options = list(TARGET_OPTIONS)
    if doc is not None and getattr(doc, "IsWorkshared", False):
        target_options.append(("Assign workset", "workset"))
    for display_name, target_key in target_options:
        item = ComboBoxItem()
        item.Content = display_name
        item.ToolTip = _TARGET_TOOLTIPS.get(target_key, "")
        cmb_target.Items.Add(item)
        target_keys.append(target_key)
    cmb_target.SelectedIndex = 0

    # --- populate category combo ---
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
            # Merge fixed scopes (Views, Sheets) with dynamic category list, sorted A-Z
            all_opts = [("Sheets", "Sheets", None), ("Views", "Views", None)]
            for name, cid in _get_category_options(doc):
                all_opts.append((name, name, cid))
            all_opts.sort(key=lambda x: x[0])
            for disp, key, cid in all_opts:
                item = ComboBoxItem()
                item.Content = disp
                cmb_category.Items.Add(item)
                category_keys.append(key)
                category_ids.append(cid)
                category_names.append(disp)
        cmb_category.IsEnabled = config["selector_enabled"]
        if cmb_category.Items.Count > 0:
            cmb_category.SelectedIndex = 0

    def _do_load_params():
        all_params, families = _get_params_by_family(doc, _selected_key(), _selected_category_id(), _target_key())
        _populate_param_list(lst_parameters, all_params, families)
        if pnl_parameters is not None:
            pnl_parameters.Visibility = Visibility.Visible

    # --- mode toggle (Transform vs Overwrite) ---
    def _refresh_mode_panels():
        is_overwrite = (rb_overwrite is not None and rb_overwrite.IsChecked == True)
        if pnl_transform is not None:
            pnl_transform.Visibility = Visibility.Collapsed if is_overwrite else Visibility.Visible
        if pnl_overwrite is not None:
            pnl_overwrite.Visibility = Visibility.Visible if is_overwrite else Visibility.Collapsed

    def _populate_workset_list():
        if lst_worksets is None:
            return
        lst_worksets.Items.Clear()
        worksets = _get_user_worksets(doc)
        if not worksets:
            item = ListBoxItem()
            item.Content = "(no user worksets found in this document)"
            item.IsEnabled = False
            item.Padding = Thickness(10, 6, 10, 6)
            lst_worksets.Items.Add(item)
            return
        for name, workset_id in worksets:
            item = ListBoxItem()
            item.Content = name
            item.Tag = workset_id
            item.Padding = Thickness(10, 6, 10, 6)
            lst_worksets.Items.Add(item)

    def _refresh_target_controls():
        _reset_param_panel()
        _refresh_category_combo()
        txt_source.Text = _source_label(_selected_key())
        if chk_ignore_groups is not None:
            chk_ignore_groups.Visibility = Visibility.Visible if _target_key() in ("instance_params", "workset") else Visibility.Collapsed
        if chk_also_rename_family is not None:
            chk_also_rename_family.Visibility = Visibility.Visible if _target_key() == "type_names" else Visibility.Collapsed
        is_workset = (_target_key() == "workset")
        if pnl_mode_toggle is not None:
            pnl_mode_toggle.Visibility = Visibility.Collapsed if is_workset else Visibility.Visible
        if pnl_workset is not None:
            pnl_workset.Visibility = Visibility.Visible if is_workset else Visibility.Collapsed
        if is_workset:
            if pnl_transform is not None:
                pnl_transform.Visibility = Visibility.Collapsed
            if pnl_overwrite is not None:
                pnl_overwrite.Visibility = Visibility.Collapsed
            _populate_workset_list()
        else:
            _refresh_mode_panels()

    _refresh_target_controls()

    result = [None]

    # --- event handlers ---
    def _on_target_changed(sender, args):
        _refresh_target_controls()

    def _on_selector_changed(sender, args):
        txt_source.Text = _source_label(_selected_key())
        _reset_param_panel()
        if _is_param_mode():
            _do_load_params()

    def _on_mode_changed(sender, args):
        _refresh_mode_panels()

    def _on_apply(sender, args):
        is_overwrite = (rb_overwrite is not None and rb_overwrite.IsChecked == True)
        overwrite_value = None
        if is_overwrite:
            overwrite_value = txt_overwrite.Text if txt_overwrite else ""

        is_workset = (_target_key() == "workset")
        workset_id = None
        if is_workset:
            selected_item = lst_worksets.SelectedItem if lst_worksets else None
            if selected_item is None or selected_item.Tag is None:
                ui.uiUtils_alert("Select a target workset from the list.", title=config["title"])
                return
            workset_id = selected_item.Tag

        param_name = ""
        if _is_param_mode():
            selected_item = lst_parameters.SelectedItem if lst_parameters else None
            if selected_item is None or selected_item.Tag is None:
                ui.uiUtils_alert("Select a parameter from the list.", title=config["title"])
                return
            param_name = str(selected_item.Tag)

        if not is_workset and not is_overwrite and not any([
            txt_find.Text, txt_prefix.Text, txt_suffix.Text
        ]):
            ui.uiUtils_alert(
                "Provide at least a Find text, Prefix, or Suffix - or switch to Overwrite mode.",
                title=config["title"],
            )
            return

        result[0] = {
            "target_key":     _target_key(),
            "target_display": _target_display(),
            "scope_key":      _selected_key(),
            "scope_display":  _selected_display(),
            "cat_id":         _selected_category_id(),
            "param_name":     param_name,
            "workset_id":     workset_id,
            "find":           txt_find.Text or "",
            "replace":        txt_replace.Text or "",
            "prefix":         txt_prefix.Text or "",
            "suffix":         txt_suffix.Text or "",
            "ignore_groups":  chk_ignore_groups.IsChecked == True,
            "is_overwrite":   is_overwrite,
            "overwrite_value": overwrite_value,
            "also_rename_family": (
                chk_also_rename_family is not None and
                chk_also_rename_family.Visibility == Visibility.Visible and
                chk_also_rename_family.IsChecked == True
            ),
        }
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    cmb_target.SelectionChanged    += _on_target_changed
    cmb_category.SelectionChanged  += _on_selector_changed
    if rb_transform is not None:
        rb_transform.Checked       += _on_mode_changed
    if rb_overwrite is not None:
        rb_overwrite.Checked       += _on_mode_changed
    btn_apply.Click                += _on_apply
    btn_cancel.Click               += _on_cancel

    if window.ShowDialog() != True:
        return None
    return result[0]


def run(script_dir, lib_path, mode):
    config = _mode_config(mode)
    inputs = show_dialog(script_dir, lib_path, mode)
    if not inputs:
        return

    scope_key      = inputs["scope_key"]
    scope_display  = inputs["scope_display"]
    target_key     = inputs["target_key"]
    target_display = inputs["target_display"]
    cat_id         = inputs["cat_id"]
    param_name     = inputs["param_name"]
    workset_id     = inputs.get("workset_id")
    find_text      = inputs["find"]
    replace_text   = inputs["replace"]
    prefix         = inputs["prefix"]
    suffix         = inputs["suffix"]
    ignore_groups  = inputs["ignore_groups"]
    is_overwrite        = inputs.get("is_overwrite", False)
    overwrite_value     = inputs.get("overwrite_value", None)
    also_rename_family  = inputs.get("also_rename_family", False)

    # Collect elements
    if target_key == "instance_params":
        elements = collect_elements_with_param(doc, scope_key, cat_id, param_name, ignore_groups)
    elif target_key == "type_params":
        elements = collect_types_with_param(doc, scope_key, cat_id, param_name)
    elif target_key == "workset":
        elements = collect_instances_for_workset(doc, scope_key, cat_id, ignore_groups)
    else:
        elements = collect_target_elements(doc, target_key, scope_key, cat_id, ignore_groups)

    if not elements:
        if scope_key in ("Current Selection", "Types (Selection)"):
            msg = "No renameable items found in the current selection."
        else:
            msg = "No {} found for {}.".format(target_display.lower(), scope_display.lower())
        ui.uiUtils_alert(msg, title=config["title"])
        return

    if target_key == "type_params":
        planned, skipped = plan_type_param_renames(
            elements, param_name, find_text, replace_text, prefix, suffix,
            overwrite_value=overwrite_value if is_overwrite else None,
        )
    elif target_key == "instance_params":
        planned, skipped = plan_param_renames(
            elements, param_name, find_text, replace_text, prefix, suffix,
            overwrite_value=overwrite_value if is_overwrite else None,
        )
    elif target_key == "workset":
        planned, skipped = plan_workset_changes(doc, elements, workset_id)
    else:
        planned, skipped = plan_renames(
            elements, find_text, replace_text, prefix, suffix,
            overwrite_value=overwrite_value if is_overwrite else None,
        )

    # Plan family renames alongside type renames when requested
    fam_planned = []
    fam_skipped = []
    if target_key == "type_names" and also_rename_family:
        if scope_key == "Current Selection":
            fam_elements = _get_selected_families(doc)
        else:
            fam_elements = _collect_families_by_category(doc, cat_id)
        if fam_elements:
            fam_planned, fam_skipped = plan_renames(
                fam_elements, find_text, replace_text, prefix, suffix,
                overwrite_value=overwrite_value if is_overwrite else None,
            )

    if not planned and not fam_planned:
        ui.uiUtils_alert(
            "No values matched the criteria.\nSkipped: {}".format(len(skipped) + len(fam_skipped)),
            title=config["title"],
        )
        return

    lines = [
        "Scope:     {}".format(scope_display),
        "Target:    {}".format(target_display),
    ]
    if target_key in ("instance_params", "type_params"):
        lines.append("Parameter: {}".format(param_name))
    if target_key == "workset":
        lines.append("Workset:   {}".format(_get_workset_name(doc, workset_id)))
    if is_overwrite:
        lines.append("Mode:      Overwrite  ->  \"{}\"".format(overwrite_value))
    lines += [
        "To update: {}".format(len(planned)),
        "Skipped:   {}".format(len(skipped)),
        "",
    ]
    show_parent_name = target_key in ("instance_params", "type_params", "workset")
    for element, old_name, new_name in planned[:300]:
        if show_parent_name:
            lines.append("{}: {}  ->  {}".format(_get_name(element) or "(unnamed)", old_name, new_name))
        else:
            lines.append("{}  ->  {}".format(old_name, new_name))
    if len(planned) > 300:
        lines.append("... and {} more".format(len(planned) - 300))
    if skipped:
        lines.append("")
        lines.append("Skipped (conflicts / invalid):")
        for old_name, new_name, reason in skipped[:50]:
            lines.append("  {}  ->  {}  [{}]".format(old_name, new_name, reason))

    if fam_planned:
        lines.append("")
        lines.append("--- Also renaming {} family name(s) ---".format(len(fam_planned)))
        for _, old_name, new_name in fam_planned[:100]:
            lines.append("{}  ->  {}".format(old_name, new_name))
        if len(fam_planned) > 100:
            lines.append("... and {} more".format(len(fam_planned) - 100))

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

    if target_key == "type_params":
        renamed, failed = apply_type_param_renames(
            doc, planned, param_name, config["transaction_title"], scope_display
        )
    elif target_key == "instance_params":
        renamed, failed = apply_param_renames(
            doc, planned, param_name, config["transaction_title"], scope_display
        )
    elif target_key == "workset":
        renamed, failed = apply_workset_changes(doc, planned, workset_id, config["transaction_title"], scope_display)
    else:
        renamed, failed = apply_renames(doc, planned, config["transaction_title"], scope_display)

    fam_renamed = []
    fam_failed = []
    if fam_planned:
        fam_renamed, fam_failed = apply_renames(
            doc, fam_planned, config["transaction_title"], scope_display + " (families)"
        )

    all_failed = failed + fam_failed
    if fam_planned:
        result_lines = [
            "Types updated:    {}".format(len(renamed)),
            "Families updated: {}".format(len(fam_renamed)),
            "Failed:           {}".format(len(all_failed)),
        ]
    else:
        result_lines = ["Updated: {}".format(len(renamed)), "Failed:  {}".format(len(all_failed))]
    if all_failed:
        result_lines += ["", "Failed (first 20):"]
        for old_name, new_name, error_text in all_failed[:20]:
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
