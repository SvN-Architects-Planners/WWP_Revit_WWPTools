# -*- coding: utf-8 -*-
from System import Int64
import clr
import os
import re
import traceback

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    StorageType,
    Transaction,
)
from Autodesk.Revit import UI


TITLE = "Sync Mass Tool"
SKIP_LABEL = "(Skip)"
CANCELLED = object()


def get_uidoc():
    try:
        return __revit__.ActiveUIDocument
    except Exception:
        pass
    try:
        from pyrevit import revit
        return revit.uidoc
    except Exception:
        return None


def get_doc():
    uidoc = get_uidoc()
    if uidoc is None:
        return None
    try:
        return uidoc.Document
    except Exception:
        return None


def alert(message, title=TITLE):
    UI.TaskDialog.Show(title, "" if message is None else str(message))


def elem_id_int(eid):
    try:
        return int(eid.Value)  # Revit 2024+
    except Exception:
        try:
            return int(eid.IntegerValue)  # Revit 2023-
        except Exception:
            return None


def collect_instances(doc, bic):
    return list(
        FilteredElementCollector(doc)
        .OfCategory(bic)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def category_matches(element, bic):
    try:
        return elem_id_int(element.Category.Id) == int(bic)
    except Exception:
        return False


def get_param_value(param):
    if param is None:
        return None
    stype = param.StorageType
    if stype == StorageType.String:
        return param.AsString()
    if stype == StorageType.Integer:
        return param.AsInteger()
    if stype == StorageType.Double:
        return param.AsDouble()
    if stype == StorageType.ElementId:
        return param.AsElementId()
    return None


def set_param_value(param, value):
    if param is None or param.IsReadOnly:
        return False

    stype = param.StorageType
    try:
        if stype == StorageType.String:
            param.Set("" if value is None else str(value))
            return True
        if stype == StorageType.Integer:
            if value is None:
                return False
            param.Set(int(value))
            return True
        if stype == StorageType.Double:
            if value is None:
                return False
            param.Set(float(value))
            return True
        if stype == StorageType.ElementId:
            if isinstance(value, ElementId):
                param.Set(value)
                return True
            try:
                param.Set(ElementId(Int64(value)))
                return True
            except Exception:
                return False
    except Exception:
        return False

    return False


def build_param_map(element):
    param_map = {}
    for param in element.Parameters:
        try:
            name = param.Definition.Name
        except Exception:
            continue
        if name and name not in param_map:
            param_map[name] = param
    return param_map


def iter_instance_params(element):
    for param in element.Parameters:
        try:
            _ = param.Definition.Name
        except Exception:
            continue
        yield param


def get_parent_mass_id(mass_floor):
    """Return the ElementId of the mass that owns this mass floor."""
    try:
        eid = mass_floor.OwningMassId
        if eid is not None and eid != ElementId.InvalidElementId:
            return eid
    except Exception:
        pass

    try:
        p = mass_floor.get_Parameter(BuiltInParameter.HOST_ID_PARAM)
        if p is not None:
            eid = p.AsElementId()
            if eid is not None and eid != ElementId.InvalidElementId:
                return eid
    except Exception:
        pass

    return None


def get_selected_mass_scope(uidoc):
    selected_mass_ids = set()
    selected_mass_floor_ids = set()
    ignored = 0

    try:
        selected_ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        selected_ids = []

    doc = uidoc.Document
    for eid in selected_ids:
        element = doc.GetElement(eid)
        if element is None:
            continue
        if category_matches(element, BuiltInCategory.OST_Mass):
            selected_mass_ids.add(elem_id_int(element.Id))
        elif category_matches(element, BuiltInCategory.OST_MassFloor):
            selected_mass_floor_ids.add(elem_id_int(element.Id))
        else:
            ignored += 1

    return selected_mass_ids, selected_mass_floor_ids, ignored


def filter_mass_floors_for_scope(mass_floors, target_mass_ids, target_mass_floor_ids):
    filtered = []
    target_mass_ids = target_mass_ids or set()
    target_mass_floor_ids = target_mass_floor_ids or set()

    for mass_floor in mass_floors:
        floor_key = elem_id_int(mass_floor.Id)
        parent_id = get_parent_mass_id(mass_floor)
        parent_key = elem_id_int(parent_id) if parent_id is not None else None
        if floor_key in target_mass_floor_ids or parent_key in target_mass_ids:
            filtered.append(mass_floor)

    return filtered


def sync_mass_floor_parameters(doc, target_mass_ids=None, target_mass_floor_ids=None, title=TITLE):
    masses = collect_instances(doc, BuiltInCategory.OST_Mass)
    mass_floors = collect_instances(doc, BuiltInCategory.OST_MassFloor)

    if not masses:
        alert("No Mass elements found.", title=title)
        return
    if not mass_floors:
        alert("No Mass Floors found.", title=title)
        return

    scoped = target_mass_ids is not None or target_mass_floor_ids is not None
    if scoped:
        mass_floors = filter_mass_floors_for_scope(mass_floors, target_mass_ids, target_mass_floor_ids)
        if not mass_floors:
            alert("No Mass Floors were found for the selected Mass or Mass Floor elements.", title=title)
            return

    mass_id_to_mass = {}
    for mass in masses:
        key = elem_id_int(mass.Id)
        if key is not None:
            mass_id_to_mass[key] = mass

    updated = 0
    skipped = 0
    no_match = 0
    missing_params = 0
    type_mismatch = 0
    synced_param_counts = {}

    t = Transaction(doc, "Sync Mass Floor Parameters")
    started = False
    try:
        t.Start()
        started = True

        for mass_floor in mass_floors:
            parent_mass_id = get_parent_mass_id(mass_floor)
            if parent_mass_id is None:
                skipped += 1
                continue

            parent_key = elem_id_int(parent_mass_id)
            mass = mass_id_to_mass.get(parent_key)
            if mass is None:
                no_match += 1
                continue

            mass_floor_params = build_param_map(mass_floor)

            wrote_any = False
            for src_param in iter_instance_params(mass):
                name = src_param.Definition.Name
                target_param = mass_floor_params.get(name)
                if target_param is None:
                    continue

                if target_param.StorageType != src_param.StorageType:
                    type_mismatch += 1
                    continue

                value = get_param_value(src_param)
                if set_param_value(target_param, value):
                    wrote_any = True
                    synced_param_counts[name] = synced_param_counts.get(name, 0) + 1

            if wrote_any:
                updated += 1
            else:
                missing_params += 1

        t.Commit()
    except Exception as exc:
        if started:
            try:
                t.RollBack()
            except Exception:
                pass
        alert("{}\n\n{}".format(exc, traceback.format_exc()), title=title + " - Error")
        return

    msg_lines = []
    if scoped:
        msg_lines.append("Mass Floors considered: {}".format(len(mass_floors)))
        msg_lines.append("")
    msg_lines.extend([
        "Updated: {}".format(updated),
        "Skipped (no parent mass link): {}".format(skipped),
        "Skipped (parent mass not in model): {}".format(no_match),
        "Skipped (missing/read-only params): {}".format(missing_params),
        "Skipped (storage type mismatch): {}".format(type_mismatch),
        "",
        "Parameters synced (name: floors written):",
    ])
    if synced_param_counts:
        for name in sorted(synced_param_counts):
            msg_lines.append("  {}: {}".format(name, synced_param_counts[name]))
    else:
        msg_lines.append("  (none)")
    alert("\n".join(msg_lines), title=title)


def run_sync_all():
    doc = get_doc()
    if doc is None:
        alert("No active Revit document found.")
        return
    sync_mass_floor_parameters(doc, title="Sync All Mass")


def run_sync_selected():
    uidoc = get_uidoc()
    if uidoc is None:
        alert("No active Revit document found.", title="Sync Selected Mass")
        return

    selected_mass_ids, selected_mass_floor_ids, ignored = get_selected_mass_scope(uidoc)
    if not selected_mass_ids and not selected_mass_floor_ids:
        alert(
            "Select one or more Mass or Mass Floor elements before running this tool.",
            title="Sync Selected Mass",
        )
        return

    sync_mass_floor_parameters(
        uidoc.Document,
        target_mass_ids=selected_mass_ids,
        target_mass_floor_ids=selected_mass_floor_ids,
        title="Sync Selected Mass",
    )


def is_writable_metric_param(param):
    if param is None or param.IsReadOnly:
        return False
    try:
        return param.StorageType in (StorageType.String, StorageType.Integer, StorageType.Double)
    except Exception:
        return False


def collect_writable_mass_param_names(masses):
    names = set()
    for mass in masses:
        for param in iter_instance_params(mass):
            if not is_writable_metric_param(param):
                continue
            try:
                name = param.Definition.Name
            except Exception:
                name = None
            if name:
                names.add(name)
    return sorted(names, key=lambda n: n.lower())


def select_one(options, title, prompt):
    try:
        import WWP_uiUtils as ui
        indices = ui.uiUtils_select_indices(
            options,
            title=title,
            prompt=prompt,
            multiselect=False,
            width=560,
            height=520,
        )
        if not indices:
            return None
        index = int(indices[0])
        if index < 0 or index >= len(options):
            return None
        return options[index]
    except Exception:
        pass

    try:
        from pyrevit import forms
        return forms.SelectFromList.show(
            options,
            title=title,
            multiselect=False,
            button_name="Select",
        )
    except Exception as exc:
        alert("Unable to load parameter picker:\n{}".format(exc), title=title)
        return None


def show_publish_mapping_dialog(param_names, xaml_dir):
    if not xaml_dir:
        return None

    xaml_path = os.path.join(xaml_dir, "PublishLevelCountsDialog.xaml")
    if not os.path.isfile(xaml_path):
        return None

    try:
        clr.AddReference("PresentationFramework")
        clr.AddReference("PresentationCore")
        clr.AddReference("WindowsBase")
        clr.AddReference("System.Xml")
        from System import String
        from System.Collections.Generic import List
        from System.IO import File, StringReader
        from System.Windows.Interop import WindowInteropHelper
        from System.Windows.Markup import XamlReader
        from System.Xml import XmlReader

        options = List[String]()
        options.Add(SKIP_LABEL)
        for name in param_names:
            options.Add(str(name))

        xaml_text = File.ReadAllText(xaml_path)
        reader = XmlReader.Create(StringReader(xaml_text))
        window = XamlReader.Load(reader)

        try:
            helper = WindowInteropHelper(window)
            uidoc = get_uidoc()
            if uidoc is not None:
                helper.Owner = uidoc.Application.MainWindowHandle
        except Exception:
            pass

        building_combo = window.FindName("BuildingParamCombo")
        count_combo = window.FindName("CountParamCombo")
        area_combo = window.FindName("AreaParamCombo")
        highest_level_combo = window.FindName("HighestLevelParamCombo")
        ok_button = window.FindName("OkButton")
        cancel_button = window.FindName("CancelButton")

        building_combo.ItemsSource = options
        count_combo.ItemsSource = options
        area_combo.ItemsSource = options
        highest_level_combo.ItemsSource = options
        building_combo.SelectedIndex = 0
        count_combo.SelectedIndex = 0
        area_combo.SelectedIndex = 0
        highest_level_combo.SelectedIndex = 0

        def ok_clicked(_sender, _args):
            window.DialogResult = True
            window.Close()

        def cancel_clicked(_sender, _args):
            window.DialogResult = False
            window.Close()

        ok_button.Click += ok_clicked
        cancel_button.Click += cancel_clicked

        if not window.ShowDialog():
            return CANCELLED

        building_param = str(building_combo.SelectedItem) if building_combo.SelectedItem is not None else SKIP_LABEL
        count_param = str(count_combo.SelectedItem) if count_combo.SelectedItem is not None else SKIP_LABEL
        area_param = str(area_combo.SelectedItem) if area_combo.SelectedItem is not None else SKIP_LABEL
        highest_level_param = str(highest_level_combo.SelectedItem) if highest_level_combo.SelectedItem is not None else SKIP_LABEL
        return {
            "building_param": None if building_param == SKIP_LABEL else building_param,
            "count_param": None if count_param == SKIP_LABEL else count_param,
            "area_param": None if area_param == SKIP_LABEL else area_param,
            "highest_level_param": None if highest_level_param == SKIP_LABEL else highest_level_param,
        }
    except Exception as exc:
        alert("Could not load the parameter mapping dialog:\n{}".format(exc), title="Publish Mass Level Counts")
        return CANCELLED


def choose_destination_parameter(param_names, metric_name, title):
    options = [SKIP_LABEL] + list(param_names)
    selected = select_one(
        options,
        title=title,
        prompt="Select destination Mass parameter for {}:".format(metric_name),
    )
    if selected is None:
        return CANCELLED
    if selected == SKIP_LABEL:
        return None
    return selected


def choose_publish_mapping(param_names, xaml_dir=None):
    result = show_publish_mapping_dialog(param_names, xaml_dir)
    if result is CANCELLED or result == CANCELLED:
        return CANCELLED
    if isinstance(result, dict):
        return result
    alert("Parameter mapping dialog could not be opened.", title="Publish Mass Level Counts")
    return CANCELLED


def get_floor_area_internal(mass_floor):
    try:
        param = mass_floor.LookupParameter("Floor Area")
        if param is not None and param.StorageType == StorageType.Double and param.AsDouble() > 0:
            return param.AsDouble()
    except Exception:
        pass

    try:
        param = mass_floor.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        if param is not None and param.StorageType == StorageType.Double and param.AsDouble() > 0:
            return param.AsDouble()
    except Exception:
        pass

    try:
        for param in mass_floor.Parameters:
            if param.StorageType != StorageType.Double:
                continue
            value = param.AsDouble()
            if value <= 0:
                continue
            name = param.Definition.Name or ""
            if "area" in name.lower():
                return value
    except Exception:
        pass

    return 0.0


def internal_area_to_square_meters(area_internal):
    try:
        from Autodesk.Revit.DB import UnitTypeId, UnitUtils
        return UnitUtils.ConvertFromInternalUnits(float(area_internal), UnitTypeId.SquareMeters)
    except Exception:
        return float(area_internal) * 0.092903


def internal_area_to_project_number(doc, area_internal):
    try:
        from Autodesk.Revit.DB import SpecTypeId, UnitUtils
        units = doc.GetUnits()
        options = units.GetFormatOptions(SpecTypeId.Area)
        unit_type_id = options.GetUnitTypeId()
        return UnitUtils.ConvertFromInternalUnits(float(area_internal), unit_type_id)
    except Exception:
        return internal_area_to_square_meters(area_internal)


def format_area_value(doc, area_internal):
    try:
        from Autodesk.Revit.DB import SpecTypeId, UnitFormatUtils
        return UnitFormatUtils.Format(doc.GetUnits(), SpecTypeId.Area, float(area_internal), False)
    except Exception:
        return "{:.2f} m2".format(internal_area_to_square_meters(area_internal))


def param_is_area(param):
    try:
        from Autodesk.Revit.DB import SpecTypeId
        data_type = param.Definition.GetDataType()
        if data_type == SpecTypeId.Area:
            return True
        try:
            return data_type.TypeId == SpecTypeId.Area.TypeId
        except Exception:
            pass
    except Exception:
        pass

    try:
        from Autodesk.Revit.DB import UnitType
        return param.Definition.UnitType == UnitType.UT_Area
    except Exception:
        return False


def set_count_param(param, count):
    if not is_writable_metric_param(param):
        return False
    try:
        if param.StorageType == StorageType.String:
            param.Set(str(int(count)))
            return True
        if param.StorageType == StorageType.Integer:
            param.Set(int(count))
            return True
        if param.StorageType == StorageType.Double:
            param.Set(float(count))
            return True
    except Exception:
        return False
    return False


def set_area_param(doc, param, area_internal):
    if not is_writable_metric_param(param):
        return False
    try:
        if param.StorageType == StorageType.String:
            param.Set(format_area_value(doc, area_internal))
            return True
        if param.StorageType == StorageType.Integer:
            param.Set(int(round(internal_area_to_project_number(doc, area_internal))))
            return True
        if param.StorageType == StorageType.Double:
            if param_is_area(param):
                param.Set(float(area_internal))
            else:
                param.Set(float(internal_area_to_project_number(doc, area_internal)))
            return True
    except Exception:
        return False
    return False


def get_highest_level_number(floors, doc):
    """Return the highest floor level number adjusted for regional and mezzanine conventions.

    Adjustments applied to the raw number extracted from the highest level's name:
      +1  if UK numbering is detected (ground floor is numbered 0, e.g. "Level 00")
      +N  where N is the number of mezzanine levels present (level name contains "mez")

    Examples:
      NA, no mez  – Level 13            → 13
      UK, no mez  – Level 12 (00=GF)   → 13  (+1 for 00-based ground)
      NA, 1 mez   – Level 13 + Mez     → 14  (+1 for mez)
      UK, 1 mez   – Level 12 + Mez     → 14  (+1 UK +1 mez)
    """
    level_info = []
    for floor in floors:
        try:
            level_id = floor.LevelId
            if level_id is None:
                continue
            level = doc.GetElement(level_id)
            if level is None:
                continue
            name = (level.Name or "").strip()
            elev = level.Elevation
            is_mez = bool(re.search(r'mez', name, re.IGNORECASE))
            match = re.search(r'\d+', name)
            num = int(match.group(0)) if match else None
            level_info.append({"elev": elev, "num": num, "name": name, "is_mez": is_mez})
        except Exception:
            pass

    if not level_info:
        return None

    # UK convention: ground floor is numbered 0 (e.g. "Level 00")
    non_mez_nums = [li["num"] for li in level_info if not li["is_mez"] and li["num"] is not None]
    is_uk = bool(non_mez_nums) and min(non_mez_nums) == 0

    mez_count = sum(1 for li in level_info if li["is_mez"])

    highest = max(level_info, key=lambda li: li["elev"])
    raw_num = highest["num"]
    if raw_num is None:
        return None

    return raw_num + (1 if is_uk else 0) + mez_count


def set_highest_level_param(param, level_num):
    if not is_writable_metric_param(param):
        return False
    try:
        if param.StorageType == StorageType.String:
            param.Set(str(level_num))
            return True
        if param.StorageType == StorageType.Integer:
            param.Set(int(level_num))
            return True
        if param.StorageType == StorageType.Double:
            param.Set(float(level_num))
            return True
    except Exception:
        return False
    return False


def get_param_by_name(element, name):
    try:
        return element.LookupParameter(name)
    except Exception:
        return None


def build_mass_floor_groups(doc):
    masses = collect_instances(doc, BuiltInCategory.OST_Mass)
    mass_floors = collect_instances(doc, BuiltInCategory.OST_MassFloor)
    groups = {}

    for mass in masses:
        key = elem_id_int(mass.Id)
        if key is not None:
            groups[key] = []

    for mass_floor in mass_floors:
        parent_id = get_parent_mass_id(mass_floor)
        parent_key = elem_id_int(parent_id) if parent_id is not None else None
        if parent_key in groups:
            groups[parent_key].append(mass_floor)

    return masses, mass_floors, groups


def publish_mass_level_metrics(xaml_dir=None):
    doc = get_doc()
    if doc is None:
        alert("No active Revit document found.", title="Publish Mass Level Counts")
        return

    masses, mass_floors, groups = build_mass_floor_groups(doc)
    if not masses:
        alert("No Mass elements found.", title="Publish Mass Level Counts")
        return
    if not mass_floors:
        alert("No Mass Floors found.", title="Publish Mass Level Counts")
        return

    param_names = collect_writable_mass_param_names(masses)
    if not param_names:
        alert("No writable Mass instance parameters were found.", title="Publish Mass Level Counts")
        return

    mapping = choose_publish_mapping(param_names, xaml_dir=xaml_dir)
    if mapping is CANCELLED or mapping == CANCELLED:
        return
    building_param_name = mapping.get("building_param")
    count_param_name = mapping.get("count_param")
    area_param_name = mapping.get("area_param")
    highest_level_param_name = mapping.get("highest_level_param")

    if not count_param_name and not area_param_name and not highest_level_param_name:
        alert("No destination parameters were selected.", title="Publish Mass Level Counts")
        return

    dest_params = [n for n in (count_param_name, area_param_name, highest_level_param_name) if n]
    if len(dest_params) != len(set(dest_params)):
        alert("Each metric must map to a different destination parameter.", title="Publish Mass Level Counts")
        return

    # Pre-compute highest level per building (or per mass when no building param is chosen).
    # Done outside the transaction since it only reads data.
    mass_highest_level = {}  # {elem_id_int: level_num}
    if highest_level_param_name:
        if building_param_name:
            # Collect active floors per building value
            building_active_floors = {}
            for mass in masses:
                val = get_param_value(get_param_by_name(mass, building_param_name))
                bval = str(val).strip() if val is not None else ""
                key = elem_id_int(mass.Id)
                active = [f for f in groups.get(key, []) if get_floor_area_internal(f) > 0]
                if bval not in building_active_floors:
                    building_active_floors[bval] = []
                building_active_floors[bval].extend(active)
            # Resolve per-building highest level and map back to each mass
            building_highest = {
                bval: get_highest_level_number(bfloors, doc) if bfloors else None
                for bval, bfloors in building_active_floors.items()
            }
            for mass in masses:
                val = get_param_value(get_param_by_name(mass, building_param_name))
                bval = str(val).strip() if val is not None else ""
                mass_highest_level[elem_id_int(mass.Id)] = building_highest.get(bval)
        else:
            # Fall back to per-mass highest level
            for mass in masses:
                key = elem_id_int(mass.Id)
                active = [f for f in groups.get(key, []) if get_floor_area_internal(f) > 0]
                mass_highest_level[key] = get_highest_level_number(active, doc) if active else None

    updated_masses = 0
    count_written = 0
    area_written = 0
    highest_level_written = 0
    no_floors = 0
    no_area = 0
    failures = 0

    t = Transaction(doc, "Publish Mass Level Counts")
    started = False
    try:
        t.Start()
        started = True

        for mass in masses:
            key = elem_id_int(mass.Id)
            all_floors = groups.get(key, [])
            # Only count floors that actually have geometry (area > 0).
            # Revit creates MassFloor elements for every checked level even when
            # the mass shape doesn't reach that level, so zero-area entries must
            # be excluded from the count and highest-level calculation.
            floors = [f for f in all_floors if get_floor_area_internal(f) > 0]
            if not floors:
                no_floors += 1
                continue

            wrote_any = False

            if count_param_name:
                param = get_param_by_name(mass, count_param_name)
                if set_count_param(param, len(floors)):
                    count_written += 1
                    wrote_any = True
                else:
                    failures += 1

            if area_param_name:
                areas = [get_floor_area_internal(floor) for floor in floors]
                areas = [area for area in areas if area > 0]
                if areas:
                    typical_area = sum(areas) / float(len(areas))
                    param = get_param_by_name(mass, area_param_name)
                    if set_area_param(doc, param, typical_area):
                        area_written += 1
                        wrote_any = True
                    else:
                        failures += 1
                else:
                    no_area += 1

            if highest_level_param_name:
                level_num = mass_highest_level.get(key)
                if level_num is not None:
                    param = get_param_by_name(mass, highest_level_param_name)
                    if set_highest_level_param(param, level_num):
                        highest_level_written += 1
                        wrote_any = True
                    else:
                        failures += 1

            if wrote_any:
                updated_masses += 1

        t.Commit()
    except Exception as exc:
        if started:
            try:
                t.RollBack()
            except Exception:
                pass
        alert("{}\n\n{}".format(exc, traceback.format_exc()), title="Publish Mass Level Counts - Error")
        return

    report = [
        "Masses updated: {}".format(updated_masses),
        "Mass Floor counts written: {}".format(count_written),
        "Typical floor areas written: {}".format(area_written),
        "Highest floor level numbers written: {}".format(highest_level_written),
        "Skipped (no associated Mass Floors): {}".format(no_floors),
        "Skipped (no Floor Area values): {}".format(no_area),
        "Failures: {}".format(failures),
        "",
        "Building grouping parameter: {}".format(building_param_name or SKIP_LABEL),
        "Count parameter: {}".format(count_param_name or SKIP_LABEL),
        "Typical floor area parameter: {}".format(area_param_name or SKIP_LABEL),
        "Highest floor level parameter: {}".format(highest_level_param_name or SKIP_LABEL),
    ]
    alert("\n".join(report), title="Publish Mass Level Counts")
