# -*- coding: utf-8 -*-
import clr
import os
import re
import traceback
from System import Int64

# Local constants used by the publish tool
TITLE = "Publish Mass Level Counts"
SKIP_LABEL = "(skip)"
# Sentinel value returned by dialog helpers when the user cancels
CANCELLED = object()


def get_uidoc():
    try:
        return __revit__.ActiveUIDocument
    except Exception:
        try:
            from pyrevit import revit
            return getattr(revit, 'uidoc', None)
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
def get_project_level_index(doc, baseline=0.0, tol=1e-6):
    """Build project-level elevation index mapping starting from `baseline`.

    Returns (elevs_at_or_above, elev_to_index) where elevs_at_or_above is a
    sorted list of unique elevations >= baseline and elev_to_index maps each
    elevation to a 1-based index.
    """
    try:
        clr.AddReference("RevitAPI")
        from Autodesk.Revit.DB import Level
    except Exception:
        Level = None

    try:
        if Level is not None:
            all_levels = list(__revit__.ActiveUIDocument.Document.GetElementsByClass(Level))
        else:
            from Autodesk.Revit.DB import FilteredElementCollector
            all_levels = list(FilteredElementCollector(doc).OfClass(type(doc.GetElement(doc.GetElementId(ElementId.InvalidElementId)))))
    except Exception:
        all_levels = []

    elevations = []
    for lvl in all_levels:
        try:
            elevations.append(float(lvl.Elevation))
        except Exception:
            continue

    if not elevations:
        return [], {}

    unique_elevs = []
    for e in sorted(elevations):
        if not unique_elevs or abs(e - unique_elevs[-1]) > tol:
            unique_elevs.append(e)

    elevs_at_or_above = [e for e in unique_elevs if e >= baseline - tol]
    if not elevs_at_or_above:
        elevs_at_or_above = unique_elevs

    elev_to_index = {e: i + 1 for i, e in enumerate(elevs_at_or_above)}
    return elevs_at_or_above, elev_to_index


def get_highest_level_number(floors, doc, project_index=None):
    """Return the highest floor level index for the given floors using a project-wide
    elevation-indexed scheme. If `project_index` is supplied, it must be the
    tuple returned by `get_project_level_index` and will be used instead of
    building a fresh mapping.
    """
    # If a project index mapping is supplied, use it; otherwise build one on demand.
    if project_index and isinstance(project_index, tuple) and len(project_index) == 2:
        elevs_at_or_above, elev_to_index = project_index
    else:
        elevs_at_or_above, elev_to_index = get_project_level_index(doc)

    if not elevs_at_or_above:
        # Fall back to name-based parsing if project index couldn't be built
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

        highest = max(level_info, key=lambda li: li["elev"])
        raw_num = highest["num"]
        return raw_num if raw_num is not None else None

    # Identify the highest associated Mass Floor first, then resolve that floor's level
    # to the project-wide index.
    highest_floor = None
    highest_floor_elev = None
    for floor in floors:
        try:
            if get_floor_area_internal(floor) <= 0:
                continue
            lid = floor.LevelId
            if lid is None:
                continue
            lvl = doc.GetElement(lid)
            if lvl is None:
                continue
            lev = float(lvl.Elevation)
            if highest_floor_elev is None or lev > highest_floor_elev:
                highest_floor = floor
                highest_floor_elev = lev
        except Exception:
            continue

    if highest_floor is None:
        return None

    try:
        lvl = doc.GetElement(highest_floor.LevelId)
        if lvl is None:
            return None
        lev = float(lvl.Elevation)
        nearest = min(elevs_at_or_above, key=lambda ue: abs(ue - lev))
        return elev_to_index[nearest]
    except Exception:
        return None
    


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


def show_publish_mapping_dialog(param_names, xaml_dir, preselect=None):
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

        # Ensure window sizes to its content and that buttons are visible
        try:
            from System.Windows import SizeToContent, Visibility
            try:
                window.SizeToContent = SizeToContent.Height
            except Exception:
                pass
        except Exception:
            Visibility = None

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

        # Apply any preselected values (match by exact string)
        try:
            if preselect and isinstance(preselect, dict):
                def set_combo_to(combo, value):
                    if not combo or not value:
                        return
                    try:
                        # find the item equal to value
                        for i in range(combo.Items.Count):
                            if str(combo.Items[i]) == str(value):
                                combo.SelectedIndex = i
                                return
                    except Exception:
                        pass

                set_combo_to(building_combo, preselect.get("building_param"))
                set_combo_to(count_combo, preselect.get("count_param"))
                set_combo_to(area_combo, preselect.get("area_param"))
                set_combo_to(highest_level_combo, preselect.get("highest_level_param"))
        except Exception:
            pass

        def ok_clicked(_sender, _args):
            window.DialogResult = True
            window.Close()

        def cancel_clicked(_sender, _args):
            window.DialogResult = False
            window.Close()

        try:
            if cancel_button is not None:
                try:
                    cancel_button.Visibility = Visibility.Visible
                except Exception:
                    pass
                cancel_button.Click += cancel_clicked
        except Exception:
            pass

        try:
            if ok_button is not None:
                try:
                    ok_button.Visibility = Visibility.Visible
                except Exception:
                    pass
                ok_button.Click += ok_clicked
        except Exception:
            pass

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
    # Try to auto-detect sensible defaults from parameter names.
    def guess_mapping(names):
        lower = [n.lower() for n in names]
        mapping = {"building_param": None, "count_param": None, "area_param": None, "highest_level_param": None}

        # building: contains 'build' or 'building'
        for n in names:
            nl = n.lower()
            if mapping["building_param"] is None and ("build" in nl or "building" in nl):
                mapping["building_param"] = n

        # count: contains 'count' or 'massfloorcount' or 'floorcount'
        for n in names:
            nl = n.lower()
            if mapping["count_param"] is None and ("count" in nl or "floorcount" in nl or "massfloor" in nl):
                mapping["count_param"] = n

        # area: contains 'area' or 'typicalfloor'
        for n in names:
            nl = n.lower()
            if mapping["area_param"] is None and ("area" in nl or "typical" in nl):
                mapping["area_param"] = n

        # highest level: contains 'highest' or 'level' with 'highest' or 'highestlevel' or 'highest_level'
        for n in names:
            nl = n.lower()
            if mapping["highest_level_param"] is None and ("highest" in nl or ("level" in nl and ("highest" in nl or "top" in nl))):
                mapping["highest_level_param"] = n

        return mapping

    guessed = guess_mapping(param_names)

    # Always show the mapping dialog, but preselect sensible defaults so the user
    # can confirm or override them without rebuilding the mapping every time.
    result = show_publish_mapping_dialog(param_names, xaml_dir, preselect=guessed)
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

    # Pre-compute highest level per-mass (each Mass uses its own highest floor).
    # This ensures separate masses (eg. twin towers) get their own highest numbers
    # even when they share a building grouping value.
    mass_highest_level = {}  # {elem_id_int: level_num}
    if highest_level_param_name:
        # Build a single project-level elevation index once and reuse for all masses
        try:
            project_index = get_project_level_index(doc)
        except Exception:
            project_index = None
        for mass in masses:
            key = elem_id_int(mass.Id)
            active = [f for f in groups.get(key, []) if get_floor_area_internal(f) > 0]
            mass_highest_level[key] = get_highest_level_number(active, doc, project_index) if active else None

    # Build preview and ask for confirmation before starting transaction
    try:
        try:
            import WWP_uiUtils as ui
            have_ui = True
        except Exception:
            have_ui = False

        preview_lines = ["Preview of changes to be written:"]
        for mass in masses:
            try:
                key = elem_id_int(mass.Id)
                all_floors = groups.get(key, [])
                floors = [f for f in all_floors if get_floor_area_internal(f) > 0]
                count_val = len(floors)
                areas = [get_floor_area_internal(f) for f in floors]
                areas = [a for a in areas if a > 0]
                typical_area = format_area_value(doc, sum(areas) / float(len(areas))) if areas else "(none)"
                highest_val = mass_highest_level.get(key)
                floor_labels = []
                for floor in floors:
                    try:
                        lid = floor.LevelId
                        lvl = doc.GetElement(lid) if lid is not None else None
                        name = (lvl.Name or "") if lvl is not None else "(no level)"
                        floor_labels.append(name)
                    except Exception:
                        continue
                try:
                    mname = getattr(mass, 'Name', None) or mass.GetType().Name
                except Exception:
                    mname = str(key)
                preview_lines.append("- {} (id={}): floors={}, area={}, highest={}, mass floors=[{}]".format(
                    mname,
                    key,
                    count_val,
                    typical_area,
                    highest_val if highest_val is not None else '(none)',
                    ", ".join(floor_labels) if floor_labels else "(none)"
                ))
            except Exception:
                continue

        # If any destination mapping is missing but we still have values, prompt user anyway
        preview_text = "\n".join(preview_lines)
        if have_ui:
            try:
                if not ui.uiUtils_confirm(preview_text, title="Publish Mass Level Counts - Preview"):
                    return
            except Exception:
                pass
    except Exception:
        pass

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
