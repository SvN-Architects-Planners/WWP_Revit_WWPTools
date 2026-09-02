import os
import sys
import traceback

import clr
from System import String
from System.Collections.Generic import List
from System.IO import File

from pyrevit import DB, revit


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)
from WWP_versioning import apply_window_title


TOOL_TITLE = "Parking Count in Room/Area"


def _elem_id_int(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.Value)


def _load_uiutils():
    try:
        import WWP_uiUtils as ui
        return ui
    except Exception:
        try:
            from pyrevit import forms
            forms.alert(
                "WWP_uiUtils is not available. Restart pyRevit or reinstall WWPTools.",
                title=TOOL_TITLE,
            )
        except Exception:
            pass
        raise


def _get_active_view(doc):
    try:
        return doc.ActiveView
    except Exception:
        return None


def _get_rooms_in_view(doc, view):
    rooms = []
    collector = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_Rooms)
    for room in collector.WhereElementIsNotElementType():
        if room is None:
            continue
        try:
            if room.Area <= 0:
                continue
        except Exception:
            pass
        rooms.append(room)
    rooms.sort(key=lambda r: ((r.Number or ""), (r.Name or "")))
    return rooms


def _get_areas_in_view(doc, view):
    areas = []
    try:
        collector = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_Areas)
        for area in collector.WhereElementIsNotElementType():
            if area is None:
                continue
            try:
                if area.Area <= 0:
                    continue
            except Exception:
                pass
            areas.append(area)
        areas.sort(key=lambda a: ((a.Number or ""), (a.Name or "")))
    except Exception:
        pass
    return areas


def _get_parkings_in_view(doc, view):
    collector = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_Parking)
    return [e for e in collector.WhereElementIsNotElementType() if e is not None]


def _get_family_name(parking, doc):
    try:
        symbol = doc.GetElement(parking.GetTypeId())
        if symbol is not None:
            family = symbol.Family
            if family is not None:
                return family.Name
    except Exception:
        pass
    return None


def _get_family_names(parkings, doc):
    names = set()
    for p in parkings:
        name = _get_family_name(p, doc)
        if name:
            names.add(name)
    return sorted(names, key=lambda n: n.lower())


def _get_location_point(elem):
    try:
        loc = elem.Location
    except Exception:
        loc = None
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    if isinstance(loc, DB.LocationCurve):
        try:
            return loc.Curve.Evaluate(0.5, True)
        except Exception:
            return None
    return None


def _build_area_loops(spatial):
    """Return (outer_pts, [hole_pts, ...]) from the area's boundary segments.

    Loop 0 is the outer boundary; loops 1+ are holes (sub-areas cut into the parent).
    Each loop is tessellated independently so the polygons are separate lists.
    """
    try:
        opts = DB.SpatialElementBoundaryOptions()
        segs = spatial.GetBoundarySegments(opts)
        if not segs:
            return [], []
        loops = []
        for loop in segs:
            pts = []
            for seg in loop:
                try:
                    for tp in seg.GetCurve().Tessellate():
                        pts.append((tp.X, tp.Y))
                except Exception:
                    pass
            if pts:
                loops.append(pts)
        outer = loops[0] if loops else []
        holes = loops[1:] if len(loops) > 1 else []
        return outer, holes
    except Exception:
        return [], []


def _pip_with_tolerance(x, y, polygon):
    """Ray-casting with 0.25 ft boundary snap - catches stalls placed exactly on edges."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]; xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    if inside:
        return True
    # Boundary snap: within 0.25 ft of any edge counts as inside
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]; xj, yj = polygon[j]
        dx = xj - xi; dy = yj - yi
        len_sq = dx * dx + dy * dy
        if len_sq > 1e-12:
            t = ((x - xi) * dx + (y - yi) * dy) / len_sq
            if t < 0.0: t = 0.0
            elif t > 1.0: t = 1.0
            px = xi + t * dx; py = yi + t * dy
            if (x - px) * (x - px) + (y - py) * (y - py) < 0.0625:
                return True
        j = i
    return False


def _pip_strict(x, y, polygon):
    """Standard ray-casting without tolerance - used for hole exclusion."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]; xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_area_loops(x, y, outer, holes):
    """True if (x,y) is inside the outer boundary and NOT inside any hole.

    Outer uses tolerance (catches stalls on boundary edges).
    Holes use strict check (keeps stalls that are on the hole edge, inside the area).
    """
    if not _pip_with_tolerance(x, y, outer):
        return False
    for hole in holes:
        if _pip_strict(x, y, hole):
            return False
    return True


def _spatial_contains_point(spatial, point, area_loops=None):
    """Check containment. For rooms uses IsPointInRoom; for areas uses polygon loops."""
    if spatial is None or point is None:
        return False
    try:
        return spatial.IsPointInRoom(point)
    except Exception:
        pass
    # area_loops = (outer, [hole, ...])
    if area_loops:
        outer, holes = area_loops
        return _point_in_area_loops(point.X, point.Y, outer, holes)
    return False


def _get_spatial_solid(doc, spatial):
    # SpatialElementGeometryCalculator only works for rooms/spaces, not areas
    try:
        calc = DB.SpatialElementGeometryCalculator(doc)
        result = calc.CalculateSpatialElementGeometry(spatial)
        return result.GetGeometry()
    except Exception:
        return None


def _get_element_solids(elem):
    solids = []
    try:
        opts = DB.Options()
        opts.DetailLevel = DB.ViewDetailLevel.Fine
        opts.ComputeReferences = False
        opts.IncludeNonVisibleObjects = False
        geom = elem.get_Geometry(opts)
    except Exception:
        geom = None
    if geom is None:
        return solids
    for obj in geom:
        solid = None
        if isinstance(obj, DB.Solid):
            solid = obj
        elif isinstance(obj, DB.GeometryInstance):
            try:
                inst_geom = obj.GetInstanceGeometry()
            except Exception:
                inst_geom = None
            if inst_geom is not None:
                for inst_obj in inst_geom:
                    if isinstance(inst_obj, DB.Solid):
                        solids.append(inst_obj)
                continue
        if solid is not None:
            solids.append(solid)
    return [s for s in solids if s is not None and s.Volume > 1e-9]


def _sum_solid_volume(solids):
    total = 0.0
    for solid in solids:
        try:
            total += solid.Volume
        except Exception:
            continue
    return total


def _intersection_volume(a_solid, b_solid):
    try:
        result = DB.BooleanOperationsUtils.ExecuteBooleanOperation(
            a_solid, b_solid, DB.BooleanOperationsType.Intersect
        )
    except Exception:
        return 0.0
    try:
        return result.Volume
    except Exception:
        return 0.0


def _get_spatial_label(spatial):
    number = ""
    name = ""
    try:
        number = spatial.Number or ""
    except Exception:
        number = ""
    # .Name throws or returns "?" for areas in IronPython; use LookupParameter instead
    try:
        raw = spatial.Name
        if raw and raw != "?":
            name = raw
    except Exception:
        pass
    if not name:
        try:
            p = spatial.LookupParameter("Name")
            if p:
                v = p.AsString()
                if v and v != "?":
                    name = v
        except Exception:
            pass
    if number and name:
        label = "{} - {}".format(number, name)
    elif name:
        label = name
    elif number:
        label = number
    else:
        label = "Space (Id:{})".format(_elem_id_int(spatial.Id))
    return label


def _get_element_bbox_2d(elem):
    """Return (x_min, y_min, x_max, y_max) of an element in model space, or None."""
    try:
        bb = elem.get_BoundingBox(None)
        if bb is not None:
            return bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y
    except Exception:
        pass
    loc = elem.Location
    pt = getattr(loc, "Point", None)
    if pt is not None:
        return pt.X, pt.Y, pt.X, pt.Y
    return None


def _sample_count_in_area(x_min, y_min, x_max, y_max, outer, holes, n=3):
    """Count how many of an n x n grid of points fall inside the area (holes-aware)."""
    count = 0
    x_range = x_max - x_min
    y_range = y_max - y_min
    x_step = x_range / n if x_range > 1e-6 else 0.0
    y_step = y_range / n if y_range > 1e-6 else 0.0
    for i in range(n):
        x = x_min + x_step * (i + 0.5) if x_step > 0 else x_min
        for j in range(n):
            y = y_min + y_step * (j + 0.5) if y_step > 0 else y_min
            if _point_in_area_loops(x, y, outer, holes):
                count += 1
    return count


def _collect_spatial_parking_map(spatials, parkings):
    spatial_map = {_elem_id_int(s.Id): [] for s in spatials}
    spatial_solids = {}
    area_loops_map = {}   # sid -> (outer, [holes])
    area_bboxes = {}      # sid -> (x_min, y_min, x_max, y_max) for pre-filtering

    for spatial in spatials:
        sid = _elem_id_int(spatial.Id)
        solid = _get_spatial_solid(spatial.Document, spatial)
        if solid is not None:
            spatial_solids[sid] = solid
        else:
            outer, holes = _build_area_loops(spatial)
            if outer:
                area_loops_map[sid] = (outer, holes)
                xs = [p[0] for p in outer]
                ys = [p[1] for p in outer]
                area_bboxes[sid] = (min(xs), min(ys), max(xs), max(ys))

    for parking in parkings:
        # --- Path 1: 3-D solid intersection for rooms (most-volume wins) ---
        solids = _get_element_solids(parking)
        if solids and _sum_solid_volume(solids) > 0:
            best_id = None
            best_volume = 0.0
            for spatial in spatials:
                s_solid = spatial_solids.get(_elem_id_int(spatial.Id))
                if s_solid is None:
                    continue
                vol = sum(_intersection_volume(s, s_solid) for s in solids)
                if vol > best_volume:
                    best_volume = vol
                    best_id = _elem_id_int(spatial.Id)
            if best_id is not None and best_volume > 0:
                spatial_map[best_id].append(parking)
                continue

        # --- Path 2: 2-D bounding-box coverage for areas (most-sampled wins) ---
        bbox = _get_element_bbox_2d(parking)
        best_sid = None
        best_count = 0
        best_area_val = None

        for spatial in spatials:
            sid = _elem_id_int(spatial.Id)
            loops = area_loops_map.get(sid)
            if loops is None:
                continue
            # Quick bbox pre-filter
            ab = area_bboxes.get(sid)
            if ab and bbox:
                px1, py1, px2, py2 = bbox
                if px2 < ab[0] or px1 > ab[2] or py2 < ab[1] or py1 > ab[3]:
                    continue
            outer, holes = loops
            if bbox:
                count = _sample_count_in_area(bbox[0], bbox[1], bbox[2], bbox[3], outer, holes)
            else:
                pt = _get_location_point(parking)
                count = 1 if pt and _point_in_area_loops(pt.X, pt.Y, outer, holes) else 0
            if count == 0:
                continue
            try:
                av = spatial.Area
            except Exception:
                av = float("inf")
            # Prefer higher coverage; tie-break by smallest area (most specific)
            if count > best_count or (count == best_count and av < best_area_val):
                best_count = count
                best_sid = sid
                best_area_val = av

        if best_sid is not None:
            spatial_map[best_sid].append(parking)
            continue

        # --- Path 3: single location-point fallback ---
        point = _get_location_point(parking)
        if point is None:
            continue
        best_sid = None
        best_area_val = None
        for spatial in spatials:
            sid = _elem_id_int(spatial.Id)
            loops = area_loops_map.get(sid)
            if not _spatial_contains_point(spatial, point, area_loops=loops):
                continue
            try:
                av = spatial.Area
            except Exception:
                av = float("inf")
            if best_area_val is None or av < best_area_val:
                best_area_val = av
                best_sid = sid
        if best_sid is not None:
            spatial_map[best_sid].append(parking)

    return spatial_map


def _get_param_names(elements):
    names = set()
    for elem in elements:
        try:
            for param in elem.Parameters:
                if param is None:
                    continue
                try:
                    pname = param.Definition.Name
                except Exception:
                    pname = None
                if pname:
                    names.add(pname)
        except Exception:
            continue
    return sorted(names, key=lambda n: n.lower())


def _get_type_param_names(elements):
    names = set()
    for elem in elements:
        try:
            elem_type = elem.Document.GetElement(elem.GetTypeId())
        except Exception:
            elem_type = None
        if elem_type is None:
            continue
        try:
            for param in elem_type.Parameters:
                if param is None:
                    continue
                try:
                    pname = param.Definition.Name
                except Exception:
                    pname = None
                if pname:
                    names.add(pname)
        except Exception:
            continue
    return sorted(names, key=lambda n: n.lower())


def _get_string_params(elem):
    options = []
    for param in elem.Parameters:
        if param is None or param.IsReadOnly:
            continue
        try:
            if param.StorageType != DB.StorageType.String:
                continue
        except Exception:
            continue
        try:
            name = param.Definition.Name
        except Exception:
            name = None
        if name:
            options.append(name)
    return sorted(set(options), key=lambda n: n.lower())


def _get_param_value(param, doc):
    if param is None:
        return None
    stype = param.StorageType
    try:
        if stype == DB.StorageType.String:
            return param.AsString()
        if stype == DB.StorageType.Integer:
            return param.AsInteger()
        if stype == DB.StorageType.Double:
            return param.AsDouble()
        if stype == DB.StorageType.ElementId:
            elem_id = param.AsElementId()
            if elem_id and _elem_id_int(elem_id) > 0:
                try:
                    elem = doc.GetElement(elem_id)
                    if elem:
                        return elem.Name
                except Exception:
                    pass
            return _elem_id_int(elem_id)
    except Exception:
        return None
    return None


def _get_param_by_name(elem, name):
    try:
        return elem.LookupParameter(name)
    except Exception:
        return None


def _get_parking_type_key(parking, doc, mode, param_name):
    if mode == "family_type":
        try:
            symbol = doc.GetElement(parking.GetTypeId())
            if symbol and symbol.Name:
                return symbol.Name
        except Exception:
            return "Type"
        return "Type"
    if mode == "instance_param":
        return _param_as_string(_get_param_by_name(parking, param_name), doc)
    if mode == "type_param":
        try:
            symbol = doc.GetElement(parking.GetTypeId())
        except Exception:
            symbol = None
        return _param_as_string(_get_param_by_name(symbol, param_name), doc)
    return "Type"


def _param_as_string(param, doc):
    value = _get_param_value(param, doc)
    if value is None:
        return ""
    return str(value)


def _get_parking_count(parking, doc, count_param_name=None, count_param_mode="instance"):
    if not count_param_name:
        return 1
    if count_param_mode == "type":
        try:
            symbol = doc.GetElement(parking.GetTypeId())
        except Exception:
            symbol = None
        count_param = _get_param_by_name(symbol, count_param_name)
    else:
        count_param = _get_param_by_name(parking, count_param_name)
    if count_param is None:
        return 1
    value = _get_param_value(count_param, doc)
    if value is None:
        return 1
    try:
        return int(value)
    except Exception:
        return 1


def _format_total(total_count):
    return str(total_count)


def _format_breakdown(type_counts):
    if not type_counts:
        return "0"
    sorted_keys = sorted(type_counts.keys(), key=lambda k: k.lower())
    lines = ["{}: {}".format(k, type_counts[k]) for k in sorted_keys]
    if len(type_counts) > 1:
        lines.append("")
        lines.append("Total: {}".format(sum(type_counts.values())))
    return "\n".join(lines)


def _show_inputs_form(
    spatial_labels,
    family_options,
    target_param_options,
    count_param_options,
    type_options,
    default_target_param,
):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    clr.AddReference("System.Xml")
    from System.IO import StringReader
    from System.Windows.Markup import XamlReader
    from System.Xml import XmlReader
    from System.Windows import Visibility

    def _to_net_list(items):
        net_list = List[String]()
        for item in items:
            net_list.Add("" if item is None else str(item))
        return net_list

    xaml_path = os.path.join(script_dir, "ParkingCountDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))
    xaml_text = File.ReadAllText(xaml_path)
    reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(reader)
    apply_window_title(window, TOOL_TITLE)

    rooms_list = window.FindName("RoomsList")
    families_list = window.FindName("FamiliesList")
    add_family_text = window.FindName("AddFamilyText")
    add_family_btn = window.FindName("AddFamilyButton")
    breakdown_check = window.FindName("BreakdownCheckBox")
    type_source_label = window.FindName("TypeSourceLabel")
    type_combo = window.FindName("TypeSourceCombo")
    room_combo = window.FindName("RoomParamCombo")
    count_combo = window.FindName("CountParamCombo")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")

    rooms_list.ItemsSource = _to_net_list(spatial_labels)
    room_combo.ItemsSource = _to_net_list(target_param_options)
    count_combo.ItemsSource = _to_net_list(count_param_options)
    type_combo.ItemsSource = _to_net_list(type_options)

    # Populate families list using Items (not ItemsSource) so we can add dynamically
    for name in family_options:
        families_list.Items.Add(name)
    families_list.SelectAll()

    if target_param_options:
        try:
            room_combo.SelectedItem = default_target_param
        except Exception:
            room_combo.SelectedIndex = 0
    if count_param_options:
        count_combo.SelectedIndex = 0
    if type_options:
        type_combo.SelectedIndex = 0

    _load_uiutils().uiUtils_load_logo(
        logo_image,
        os.path.join(lib_path, "WWPtools-logo.png"),
    )

    def _update_breakdown_visibility(_sender, _args):
        checked = breakdown_check.IsChecked == True
        vis = Visibility.Visible if checked else Visibility.Collapsed
        type_source_label.Visibility = vis
        type_combo.Visibility = vis

    breakdown_check.Checked += _update_breakdown_visibility
    breakdown_check.Unchecked += _update_breakdown_visibility

    def _on_add_family(_sender, _args):
        name = ""
        try:
            name = add_family_text.Text.strip() if add_family_text.Text else ""
        except Exception:
            pass
        if not name:
            return
        existing = [str(families_list.Items[i]) for i in range(families_list.Items.Count)]
        if name not in existing:
            families_list.Items.Add(name)
            families_list.ScrollIntoView(name)
        # Select via SelectedItems - works regardless of virtualisation
        try:
            if name not in [str(s) for s in families_list.SelectedItems]:
                families_list.SelectedItems.Add(name)
        except Exception:
            pass
        add_family_text.Text = ""

    add_family_btn.Click += _on_add_family

    def _ok(_sender, _args):
        window.DialogResult = True
        window.Close()

    def _cancel(_sender, _args):
        window.DialogResult = False
        window.Close()

    ok_button.Click += _ok
    cancel_button.Click += _cancel

    if not window.ShowDialog():
        return None

    selected_spatial_labels = [str(item) for item in rooms_list.SelectedItems]
    # Use SelectedItems directly - ContainerFromIndex returns None for virtualised items
    selected_family_names = [str(item) for item in families_list.SelectedItems]
    breakdown = breakdown_check.IsChecked == True
    return {
        "spatials": selected_spatial_labels,
        "families": selected_family_names,
        "breakdown": breakdown,
        "type_source": str(type_combo.SelectedItem) if type_combo.SelectedItem is not None else None,
        "target_param": str(room_combo.SelectedItem) if room_combo.SelectedItem is not None else None,
        "count_param": str(count_combo.SelectedItem) if count_combo.SelectedItem is not None else None,
    }


def main():
    ui = _load_uiutils()
    doc = revit.doc
    if doc is None:
        ui.uiUtils_alert("No active document.", title=TOOL_TITLE)
        return

    view = _get_active_view(doc)
    if view is None:
        ui.uiUtils_alert("No active view.", title=TOOL_TITLE)
        return

    rooms = _get_rooms_in_view(doc, view)
    areas = _get_areas_in_view(doc, view)
    spatials = rooms + areas
    if not spatials:
        ui.uiUtils_alert(
            "No rooms or areas found in the current view.",
            title=TOOL_TITLE,
        )
        return

    parkings = _get_parkings_in_view(doc, view)
    if not parkings:
        ui.uiUtils_alert(
            "No parking elements found in the current view.",
            title=TOOL_TITLE,
        )
        return

    spatial_parking = _collect_spatial_parking_map(spatials, parkings)
    spatials_with_parking = [s for s in spatials if spatial_parking.get(_elem_id_int(s.Id))]
    if not spatials_with_parking:
        ui.uiUtils_alert(
            "No rooms or areas with parking found in the current view.",
            title=TOOL_TITLE,
        )
        return

    family_options = _get_family_names(parkings, doc)
    if not family_options:
        ui.uiUtils_alert(
            "Could not read family names from parking elements.",
            title=TOOL_TITLE,
        )
        return

    target_param_options = _get_string_params(spatials_with_parking[0])
    if not target_param_options:
        ui.uiUtils_alert(
            "No writable text parameters found on rooms/areas.",
            title=TOOL_TITLE,
        )
        return

    instance_param_names = _get_param_names(parkings)
    type_param_names = _get_type_param_names(parkings)
    type_options = ["Family Type"] + ["Type Parameter: " + n for n in type_param_names] + [
        "Instance Parameter: " + n for n in instance_param_names
    ]
    count_param_options = ["(Default 1)"] + ["Type Parameter: " + n for n in type_param_names] + [
        "Instance Parameter: " + n for n in instance_param_names
    ]

    default_target_param = "Parking Count"
    spatial_labels = [_get_spatial_label(s) for s in spatials_with_parking]
    spatial_lookup = dict(zip(spatial_labels, spatials_with_parking))

    default_value = (
        default_target_param if default_target_param in target_param_options
        else target_param_options[0]
    )
    inputs = _show_inputs_form(
        spatial_labels,
        family_options,
        target_param_options,
        count_param_options,
        type_options,
        default_value,
    )
    if not inputs:
        return

    selected_labels = inputs.get("spatials") or []
    selected_spatials = [spatial_lookup[lbl] for lbl in selected_labels if lbl in spatial_lookup]
    if not selected_spatials:
        return

    selected_families = set(inputs.get("families") or [])

    target_param_name = inputs.get("target_param")
    if not target_param_name:
        return

    selected_count_param = inputs.get("count_param")
    if not selected_count_param:
        return
    if selected_count_param == "(Default 1)":
        count_param_name = None
        count_param_mode = "instance"
    elif selected_count_param.startswith("Type Parameter: "):
        count_param_name = selected_count_param.replace("Type Parameter: ", "", 1)
        count_param_mode = "type"
    else:
        count_param_name = selected_count_param.replace("Instance Parameter: ", "", 1)
        count_param_mode = "instance"

    breakdown = inputs.get("breakdown", False)
    selected_type_option = inputs.get("type_source") if breakdown else None
    if breakdown and selected_type_option:
        if selected_type_option == "Family Type":
            type_mode = "family_type"
            type_param_name = None
        elif selected_type_option.startswith("Type Parameter: "):
            type_mode = "type_param"
            type_param_name = selected_type_option.replace("Type Parameter: ", "", 1)
        else:
            type_mode = "instance_param"
            type_param_name = selected_type_option.replace("Instance Parameter: ", "", 1)
    else:
        type_mode = "family_type"
        type_param_name = None

    updated = 0
    skipped = 0
    failures = 0
    tx = DB.Transaction(doc, "Parking Count in Room/Area")
    try:
        tx.Start()
        for spatial in selected_spatials:
            parking_list = spatial_parking.get(_elem_id_int(spatial.Id), [])
            # Filter by selected families
            if selected_families:
                parking_list = [
                    p for p in parking_list
                    if _get_family_name(p, doc) in selected_families
                ]
            if not parking_list:
                skipped += 1
                continue

            type_counts = {}
            for parking in parking_list:
                if breakdown:
                    key = _get_parking_type_key(parking, doc, type_mode, type_param_name) or "Type"
                else:
                    key = "total"
                count = _get_parking_count(
                    parking,
                    doc,
                    count_param_name=count_param_name,
                    count_param_mode=count_param_mode,
                )
                type_counts[key] = type_counts.get(key, 0) + count

            total_count = sum(type_counts.values())
            if breakdown:
                value = _format_breakdown(type_counts)
            else:
                value = _format_total(total_count)

            param = _get_param_by_name(spatial, target_param_name)
            if param is None or param.IsReadOnly:
                failures += 1
                continue
            try:
                param.Set(value)
                updated += 1
            except Exception:
                failures += 1

        tx.Commit()
    except Exception:
        try:
            tx.RollBack()
        except Exception:
            pass
        ui.uiUtils_alert(traceback.format_exc(), title=TOOL_TITLE)
        return

    report = [
        "Spaces updated: {}".format(updated),
        "Spaces skipped: {}".format(skipped),
        "Failures: {}".format(failures),
        "",
        "Families included: {}".format(", ".join(sorted(selected_families)) if selected_families else "all"),
        "Target parameter: {}".format(target_param_name),
        "Count source: {}".format(selected_count_param),
        "Type breakdowns: {}".format("Yes ({})".format(selected_type_option) if breakdown else "No"),
    ]
    ui.uiUtils_show_text_report(
        "{} - Results".format(TOOL_TITLE),
        "\n".join(report),
        ok_text="Close",
        cancel_text=None,
        width=620,
        height=380,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui = _load_uiutils()
        ui.uiUtils_alert(traceback.format_exc(), title=TOOL_TITLE)
