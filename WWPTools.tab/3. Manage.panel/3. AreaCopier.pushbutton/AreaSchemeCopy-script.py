import os
import sys
import traceback

from pyrevit import DB, revit

from System.IO import File, StringReader
from System import Uri
from System.Windows import RoutedEventHandler, Visibility
from System.Windows.Controls import ListBoxItem
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Xml import XmlReader

script_dir = os.path.dirname(__file__)


def _find_lib_path(start_dir):
    current = os.path.abspath(start_dir)
    for _ in range(8):
        candidate = os.path.join(current, "lib")
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(os.path.join(start_dir, "..", "..", "..", "lib"))


lib_path = _find_lib_path(script_dir)
if lib_path not in sys.path:
    sys.path.append(lib_path)
import WWP_colorSchemeUtils as csu
from WWP_settings import get_tool_settings
from WWP_versioning import apply_window_title


def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-


def _load_uiutils():
    try:
        import WWP_uiUtils as ui
        return ui
    except Exception:
        try:
            from pyrevit import forms
            forms.alert(
                "WWP_uiUtils is not available. Restart pyRevit or reinstall WWPTools.",
                title="Copy Areas",
            )
        except Exception:
            pass
        raise


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = revit.uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _index_by_value(values, target, default_index=0):
    if not target:
        return default_index
    try:
        return values.index(target)
    except Exception:
        return default_index


def _show_setup_dialog(scheme_names, level_names, defaults=None):
    defaults = defaults or {}
    xaml_path = os.path.join(script_dir, "AreaSchemeCopySetup.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    xaml_reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(xaml_reader)
    apply_window_title(window, "Copy Areas - Setup")
    _set_owner(window)

    source_combo = window.FindName("SourceSchemeCombo")
    target_combo = window.FindName("TargetSchemeCombo")
    levels_list = window.FindName("LevelsList")
    tag_toggle = window.FindName("TagUntaggedToggle")
    color_toggle = window.FindName("CopyColorSchemeToggle")
    validation = window.FindName("ValidationText")
    ok_btn = window.FindName("OkButton")
    cancel_btn = window.FindName("CancelButton")
    logo_image = window.FindName("LogoImage")

    for name in scheme_names:
        source_combo.Items.Add(name)
        target_combo.Items.Add(name)
    for i, level_name in enumerate(level_names):
        item = ListBoxItem()
        item.Content = level_name
        item.Tag = i
        levels_list.Items.Add(item)

    source_default = defaults.get("source_scheme_name")
    target_default = defaults.get("target_scheme_name")
    selected_levels = set(defaults.get("selected_level_names") or [])

    if source_combo.Items.Count > 0:
        source_combo.SelectedIndex = _index_by_value(scheme_names, source_default, 0)
    if target_combo.Items.Count > 1:
        fallback_index = 1
    else:
        fallback_index = 0
    if target_combo.Items.Count > 0:
        target_combo.SelectedIndex = _index_by_value(scheme_names, target_default, fallback_index)

    for item in levels_list.Items:
        try:
            if item.Content in selected_levels:
                item.IsSelected = True
        except Exception:
            pass

    try:
        tag_toggle.IsChecked = bool(defaults.get("copy_tags", True))
    except Exception:
        pass
    try:
        color_toggle.IsChecked = bool(defaults.get("copy_color_scheme", False))
    except Exception:
        pass

    try:
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

    result = {"ok": False}

    def _set_validation(msg):
        if msg:
            validation.Text = msg
            validation.Visibility = Visibility.Visible
        else:
            validation.Text = ""
            validation.Visibility = Visibility.Collapsed

    def _on_ok(sender, args):
        source_index = int(source_combo.SelectedIndex)
        target_index = int(target_combo.SelectedIndex)
        level_indices = []
        for selected_item in levels_list.SelectedItems:
            try:
                level_indices.append(int(selected_item.Tag))
            except Exception:
                pass
        if source_index < 0 or target_index < 0:
            _set_validation("Please select both source and target area schemes.")
            return
        if source_index == target_index:
            _set_validation("Source and target area schemes must be different.")
            return
        if not level_indices:
            _set_validation("Select at least one level.")
            return
        result["ok"] = True
        result["source_scheme_index"] = source_index
        result["target_scheme_index"] = target_index
        result["selected_level_indices"] = level_indices
        result["copy_tags"] = bool(tag_toggle.IsChecked)
        result["copy_color_scheme"] = bool(color_toggle.IsChecked)
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    ok_btn.Click += RoutedEventHandler(_on_ok)
    cancel_btn.Click += RoutedEventHandler(_on_cancel)

    if window.ShowDialog() != True:
        return None
    return result if result.get("ok") else None


def _get_area_schemes(doc):
    schemes = list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme))
    schemes = [s for s in schemes if s is not None]
    schemes.sort(key=lambda s: (s.Name or "").lower())
    return schemes


def _get_levels(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).WhereElementIsNotElementType())
    levels.sort(key=lambda l: l.Elevation)
    return levels


def _get_area_plans_by_level(doc, scheme_id):
    plans = {}
    views = DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan)
    for view in views:
        try:
            if view.ViewType != DB.ViewType.AreaPlan:
                continue
        except Exception:
            continue
        if view.IsTemplate:
            continue

        view_scheme_id = None
        try:
            if hasattr(view, "AreaSchemeId"):
                view_scheme_id = view.AreaSchemeId
        except Exception:
            view_scheme_id = None
        if view_scheme_id is None:
            try:
                if hasattr(view, "AreaScheme") and view.AreaScheme:
                    view_scheme_id = view.AreaScheme.Id
            except Exception:
                view_scheme_id = None
        if view_scheme_id is None:
            try:
                param = view.get_Parameter(DB.BuiltInParameter.VIEW_AREA_SCHEME)
                if param:
                    view_scheme_id = param.AsElementId()
            except Exception:
                view_scheme_id = None

        if view_scheme_id is None or view_scheme_id != scheme_id:
            continue

        level_id = None
        try:
            if view.GenLevel:
                level_id = view.GenLevel.Id
        except Exception:
            level_id = None
        if level_id is None:
            try:
                level_id = view.LevelId
            except Exception:
                level_id = None
        if level_id is None:
            try:
                level_id = view.AssociatedLevelId
            except Exception:
                level_id = None

        if level_id is not None and level_id not in plans:
            plans[level_id] = view
    return plans


def _get_area_boundary_category_ids():
    ids = set()
    for name in ("OST_AreaSchemeLines", "OST_AreaBoundaryLines"):
        try:
            ids.add(int(getattr(DB.BuiltInCategory, name)))
        except Exception:
            pass
    return ids


def _get_boundary_curves(doc, view):
    curves = []
    boundary_ids = _get_area_boundary_category_ids()
    elements = DB.FilteredElementCollector(doc, view.Id).OfClass(DB.CurveElement)
    for elem in elements:
        cat = elem.Category
        if not cat:
            continue
        if boundary_ids:
            try:
                if _elem_id_int(cat.Id) not in boundary_ids:
                    continue
            except Exception:
                continue
        else:
            name = cat.Name or ""
            if "Area" not in name or "Boundary" not in name:
                continue
        try:
            curve = elem.GeometryCurve
        except Exception:
            curve = None
        if curve is None:
            continue
        curves.append(curve)
    return curves


def _ensure_sketch_plane(doc, view, fallback_elevation=None):
    try:
        if view.SketchPlane:
            return view.SketchPlane
    except Exception:
        pass
    if fallback_elevation is None:
        try:
            level = view.GenLevel
        except Exception:
            level = None
        elevation = level.Elevation if level else 0
    else:
        elevation = fallback_elevation
    plane = DB.Plane.CreateByNormalAndOrigin(DB.XYZ.BasisZ, DB.XYZ(0, 0, elevation))
    return DB.SketchPlane.Create(doc, plane)


def _get_plane_elevation(sketch_plane, fallback=0.0):
    if sketch_plane is None:
        return fallback
    try:
        return float(sketch_plane.GetPlane().Origin.Z)
    except Exception:
        return fallback


def _move_curve_to_elevation(curve, target_elevation):
    if curve is None:
        return None
    try:
        source_elevation = float(curve.GetEndPoint(0).Z)
    except Exception:
        source_elevation = float(target_elevation)
    dz = float(target_elevation) - source_elevation
    if abs(dz) <= 1e-9:
        return curve
    try:
        xf = DB.Transform.CreateTranslation(DB.XYZ(0, 0, dz))
        return curve.CreateTransformed(xf)
    except Exception:
        return curve


def _get_area_location_uv(area, view):
    try:
        loc = area.Location
    except Exception:
        loc = None
    if isinstance(loc, DB.LocationPoint):
        pt = loc.Point
        return DB.UV(pt.X, pt.Y)
    try:
        bbox = area.get_BoundingBox(view)
    except Exception:
        bbox = None
    if bbox is None:
        try:
            bbox = area.get_BoundingBox(None)
        except Exception:
            bbox = None
    if bbox is None:
        return None
    center = (bbox.Min + bbox.Max) / 2
    return DB.UV(center.X, center.Y)


def _get_param_value(param):
    if param is None:
        return None
    stype = param.StorageType
    if stype == DB.StorageType.String:
        try:
            return param.AsString()
        except Exception:
            return None
    if stype == DB.StorageType.Double:
        try:
            return param.AsDouble()
        except Exception:
            return None
    if stype == DB.StorageType.Integer:
        try:
            return param.AsInteger()
        except Exception:
            return None
    if stype == DB.StorageType.ElementId:
        try:
            return param.AsElementId()
        except Exception:
            return None
    return None


def _set_param_value(param, value):
    if param is None or param.IsReadOnly:
        return False
    stype = param.StorageType
    try:
        if stype == DB.StorageType.String:
            param.Set("" if value is None else str(value))
            return True
        if stype == DB.StorageType.Double:
            if value is None:
                return False
            param.Set(float(value))
            return True
        if stype == DB.StorageType.Integer:
            if value is None:
                return False
            param.Set(int(value))
            return True
        if stype == DB.StorageType.ElementId:
            if isinstance(value, DB.ElementId):
                param.Set(value)
                return True
    except Exception:
        return False
    return False


def _copy_parameters(source, target, skip_names):
    copied = 0
    for param in source.Parameters:
        if param is None or param.IsReadOnly:
            continue
        name = None
        try:
            name = param.Definition.Name
        except Exception:
            name = None
        if not name or name in skip_names:
            continue
        target_param = None
        try:
            target_param = target.LookupParameter(name)
        except Exception:
            target_param = None
        if not target_param or target_param.IsReadOnly:
            continue
        value = _get_param_value(param)
        if value is None:
            continue
        if _set_param_value(target_param, value):
            copied += 1
    return copied


def _collect_area_tags(doc, view):
    tags = []
    try:
        tags = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.AreaTag))
    except Exception:
        try:
            tags = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.SpatialElementTag))
        except Exception:
            tags = []
    return tags


def _collect_areas_by_scheme_level(doc, scheme_id, level_id):
    areas = []
    for elem in DB.FilteredElementCollector(doc).OfClass(DB.SpatialElement):
        if not isinstance(elem, DB.Area):
            continue
        area = elem
        try:
            if area.AreaScheme.Id != scheme_id:
                continue
        except Exception:
            continue
        try:
            if area.LevelId != level_id:
                continue
        except Exception:
            continue
        areas.append(area)
    return areas


def _get_tag_area_id(tag):
    try:
        if hasattr(tag, "TaggedLocalElementId"):
            return tag.TaggedLocalElementId
    except Exception:
        pass
    try:
        if hasattr(tag, "TaggedElementId"):
            link_id = tag.TaggedElementId
            try:
                return link_id.HostElementId
            except Exception:
                return link_id
    except Exception:
        pass
    try:
        ids = tag.GetTaggedLocalElementIds()
        if ids and len(ids) > 0:
            return ids[0]
    except Exception:
        pass
    return None


def _create_area_tag(doc, view, area, point, source_tag=None):
    new_tag = None
    try:
        uv = DB.UV(point.X, point.Y)
        new_tag = doc.Create.NewAreaTag(view, area, uv)
    except Exception:
        new_tag = None

    if new_tag is None:
        try:
            reference = DB.Reference(area)
            new_tag = DB.SpatialElementTag.Create(
                doc,
                view.Id,
                reference,
                False,
                DB.TagOrientation.Horizontal,
                point,
            )
        except Exception:
            new_tag = None

    if new_tag is not None and source_tag is not None:
        try:
            new_tag.ChangeTypeId(source_tag.GetTypeId())
        except Exception:
            pass
    return new_tag


def _tag_untagged_areas(doc, view, areas):
    tagged_ids = set()
    for tag in _collect_area_tags(doc, view):
        area_id = _get_tag_area_id(tag)
        if area_id is not None:
            tagged_ids.add(area_id)

    created = 0
    failed = 0
    for area in areas:
        if area.Id in tagged_ids:
            continue
        uv = _get_area_location_uv(area, view)
        if uv is None:
            failed += 1
            continue
        point = DB.XYZ(uv.U, uv.V, 0.0)
        new_tag = _create_area_tag(doc, view, area, point, source_tag=None)
        if new_tag is not None:
            created += 1
            tagged_ids.add(area.Id)
        else:
            failed += 1
    return created, failed


def _pick_index(ui, items, title, prompt):
    selected = ui.uiUtils_select_indices(
        items,
        title=title,
        prompt=prompt,
        multiselect=False,
        width=520,
        height=420,
    )
    return selected[0] if selected else -1


def _pick_levels(ui, items, title, prompt):
    selected = ui.uiUtils_select_indices(
        items,
        title=title,
        prompt=prompt,
        multiselect=True,
        width=720,
        height=620,
    )
    return selected or []


def _collect_color_fill_schemes(doc):
    try:
        return list(DB.FilteredElementCollector(doc).OfClass(DB.ColorFillScheme).ToElements())
    except Exception:
        return []


def _element_id_value(elem_id):
    try:
        return int(_elem_id_int(elem_id))
    except Exception:
        return None


def _same_element_id(a, b):
    av = _element_id_value(a)
    bv = _element_id_value(b)
    return av is not None and bv is not None and av == bv


def _get_area_scheme_id_from_color_scheme(scheme):
    try:
        if hasattr(scheme, "AreaSchemeId"):
            return scheme.AreaSchemeId
    except Exception:
        pass
    try:
        method = getattr(scheme, "GetAreaSchemeId", None)
        if callable(method):
            return method()
    except Exception:
        pass
    return None


def _copy_color_fill_scheme_data(source, target):
    for attr in ("Title", "IsByRange", "IsByValue", "IsByPercentage"):
        if hasattr(source, attr) and hasattr(target, attr):
            try:
                setattr(target, attr, getattr(source, attr))
            except Exception:
                pass

    try:
        source_entries = list(source.GetEntries())
    except Exception:
        return False

    try:
        clear_entries = getattr(target, "ClearEntries", None)
        if callable(clear_entries):
            clear_entries()
        else:
            remove_entry = getattr(target, "RemoveEntry", None)
            if callable(remove_entry):
                for entry in list(target.GetEntries()):
                    remove_entry(entry)
    except Exception:
        pass

    try:
        set_entries = getattr(target, "SetEntries", None)
        if callable(set_entries):
            set_entries(source_entries)
            return True
    except Exception:
        pass

    add_entry = getattr(target, "AddEntry", None)
    if not callable(add_entry):
        return False
    for entry in source_entries:
        try:
            clone = getattr(entry, "Clone", None)
            new_entry = clone() if callable(clone) else entry
            add_entry(new_entry)
        except Exception:
            continue
    return True


def _get_view_color_fill_scheme_id(view, category_id):
    try:
        method = getattr(view, "GetColorFillSchemeId", None)
        if callable(method):
            return method(category_id)
    except Exception:
        pass
    return None


def _set_view_color_fill_scheme_id(view, category_id, scheme_id):
    try:
        method = getattr(view, "SetColorFillSchemeId", None)
        if callable(method):
            method(category_id, scheme_id)
            return True
    except Exception:
        pass
    return False


def _find_matching_target_color_scheme(doc, source_scheme, target_area_scheme_id):
    source_name = getattr(source_scheme, "Name", "") or ""
    source_category_id = getattr(source_scheme, "CategoryId", None)
    for scheme in _collect_color_fill_schemes(doc):
        try:
            if getattr(scheme, "Name", "") != source_name:
                continue
            if source_category_id and not _same_element_id(getattr(scheme, "CategoryId", None), source_category_id):
                continue
            scheme_area_scheme_id = _get_area_scheme_id_from_color_scheme(scheme)
            if not _same_element_id(scheme_area_scheme_id, target_area_scheme_id):
                continue
            return scheme
        except Exception:
            continue
    return None


def _list_target_area_color_schemes(doc, target_area_scheme_id):
    area_category_id = DB.ElementId(DB.BuiltInCategory.OST_Areas)
    matches = []
    for scheme in _collect_color_fill_schemes(doc):
        try:
            if not _same_element_id(_get_area_scheme_id_from_color_scheme(scheme), target_area_scheme_id):
                continue
            if not _same_element_id(getattr(scheme, "CategoryId", None), area_category_id):
                continue
            matches.append(scheme)
        except Exception:
            continue
    return matches


def _create_target_color_scheme(doc, source_scheme, target_area_scheme_id):
    candidates = _list_target_area_color_schemes(doc, target_area_scheme_id)
    if not candidates:
        return None, "target area scheme has no area color scheme to duplicate from"
    template = candidates[0]
    source_name = getattr(source_scheme, "Name", "") or "Color Scheme"
    try:
        new_id = template.Duplicate(source_name)
        created = doc.GetElement(new_id)
        if created is None:
            return None, "failed to create target color scheme"
        return created, ""
    except Exception as ex:
        return None, "failed to create target color scheme ({})".format(ex)


def _copy_view_area_color_scheme(doc, source_view, target_view, target_area_scheme_id):
    return csu.copy_view_area_color_scheme_with_scope(
        doc, source_view, target_view, target_area_scheme_id
    )


def main():
    ui = _load_uiutils()
    doc = revit.doc
    if doc is None:
        ui.uiUtils_alert("No active document.", title="Copy Areas")
        return

    tool_config, save_tool_config = get_tool_settings("AreaCopier", doc=doc)

    schemes = _get_area_schemes(doc)
    if not schemes:
        ui.uiUtils_alert("No area schemes found in this project.", title="Copy Areas")
        return

    levels = _get_levels(doc)
    if not levels:
        ui.uiUtils_alert("No levels found in this project.", title="Copy Areas")
        return

    setup = _show_setup_dialog(
        [s.Name for s in schemes],
        [l.Name for l in levels],
        defaults={
            "source_scheme_name": getattr(tool_config, "source_scheme_name", None),
            "target_scheme_name": getattr(tool_config, "target_scheme_name", None),
            "selected_level_names": getattr(tool_config, "selected_level_names", None),
            "copy_tags": getattr(tool_config, "copy_tags", True),
            "copy_color_scheme": getattr(tool_config, "copy_color_scheme", False),
        },
    )
    if not setup:
        return

    source_index = int(setup.get("source_scheme_index", -1))
    target_index = int(setup.get("target_scheme_index", -1))
    level_indices = setup.get("selected_level_indices") or []
    tag_untagged = bool(setup.get("copy_tags", False))
    copy_color_scheme = bool(setup.get("copy_color_scheme", False))

    source_scheme = schemes[source_index]
    target_scheme = schemes[target_index]
    selected_levels = [levels[i] for i in level_indices if 0 <= i < len(levels)]

    tool_config.source_scheme_name = source_scheme.Name
    tool_config.target_scheme_name = target_scheme.Name
    tool_config.selected_level_names = [level.Name for level in selected_levels]
    tool_config.copy_tags = tag_untagged
    tool_config.copy_color_scheme = copy_color_scheme
    save_tool_config()

    source_plans = _get_area_plans_by_level(doc, source_scheme.Id)
    target_plans = _get_area_plans_by_level(doc, target_scheme.Id)

    report_lines = []
    total_areas = 0
    total_boundaries = 0
    total_tags = 0
    total_color_scheme_views = 0
    total_failed = 0

    transaction = DB.Transaction(doc, "Copy Areas Between Schemes")
    try:
        transaction.Start()
        for level in selected_levels:
            level_id = level.Id
            source_view = source_plans.get(level_id)
            if not source_view:
                report_lines.append("{}: no source area plan found".format(level.Name))
                continue

            target_view = target_plans.get(level_id)
            if not target_view:
                try:
                    target_view = DB.ViewPlan.CreateAreaPlan(doc, target_scheme.Id, level_id)
                    target_plans[level_id] = target_view
                except Exception as ex:
                    report_lines.append("{}: failed to create target area plan ({})".format(level.Name, ex))
                    total_failed += 1
                    continue

            color_scheme_note = ""
            if copy_color_scheme:
                ok_scheme, scheme_msg = _copy_view_area_color_scheme(
                    doc, source_view, target_view, target_scheme.Id
                )
                if ok_scheme:
                    total_color_scheme_views += 1
                else:
                    total_failed += 1
                    color_scheme_note = " | color scheme: {}".format(scheme_msg)

            boundary_curves = _get_boundary_curves(doc, source_view)
            created_boundaries = 0
            failed_boundaries = 0
            if boundary_curves:
                source_plane = None
                try:
                    source_plane = source_view.SketchPlane
                except Exception:
                    source_plane = None
                source_elevation = _get_plane_elevation(
                    source_plane,
                    fallback=getattr(boundary_curves[0].GetEndPoint(0), "Z", 0.0),
                )
                sketch_plane = _ensure_sketch_plane(doc, target_view, fallback_elevation=source_elevation)
                target_elevation = _get_plane_elevation(sketch_plane)
                for curve in boundary_curves:
                    try:
                        target_curve = _move_curve_to_elevation(curve, target_elevation)
                        doc.Create.NewAreaBoundaryLine(sketch_plane, target_curve, target_view)
                        created_boundaries += 1
                    except Exception:
                        failed_boundaries += 1
            total_boundaries += created_boundaries
            total_failed += failed_boundaries

            areas = _collect_areas_by_scheme_level(doc, source_scheme.Id, level_id)

            for area in areas:
                uv = _get_area_location_uv(area, source_view)
                if uv is None:
                    total_failed += 1
                    continue
                try:
                    new_area = doc.Create.NewArea(target_view, uv)
                except Exception:
                    total_failed += 1
                    continue

                try:
                    new_area.Name = area.Name
                except Exception:
                    pass
                try:
                    if hasattr(area, "Number"):
                        new_area.Number = area.Number
                except Exception:
                    pass

                skip_names = {"Area", "Perimeter", "Level", "Name", "Number"}
                _copy_parameters(area, new_area, skip_names)
                total_areas += 1

            if tag_untagged:
                target_areas = _collect_areas_by_scheme_level(doc, target_scheme.Id, level_id)
                created_tags, failed_tags = _tag_untagged_areas(doc, target_view, target_areas)
                total_tags += created_tags
                total_failed += failed_tags
                boundary_note = ""
                if failed_boundaries:
                    boundary_note = " ({} failed)".format(failed_boundaries)
                report_lines.append("{}: areas {} | boundaries {}{} | tagged {}{}".format(
                    level.Name,
                    len(areas),
                    created_boundaries,
                    boundary_note,
                    created_tags,
                    color_scheme_note,
                ))
            else:
                boundary_note = ""
                if failed_boundaries:
                    boundary_note = " ({} failed)".format(failed_boundaries)
                report_lines.append("{}: areas {} | boundaries {}{}{}".format(
                    level.Name,
                    len(areas),
                    created_boundaries,
                    boundary_note,
                    color_scheme_note,
                ))

        transaction.Commit()
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        ui.uiUtils_alert(traceback.format_exc(), title="Copy Areas")
        return

    report_lines.append("")
    report_lines.append("Created areas: {}".format(total_areas))
    report_lines.append("Created boundaries: {}".format(total_boundaries))
    report_lines.append("Created tags: {}".format(total_tags if tag_untagged else 0))
    report_lines.append("Applied color schemes to target views: {}".format(
        total_color_scheme_views if copy_color_scheme else 0
    ))
    report_lines.append("Failures: {}".format(total_failed))

    ui.uiUtils_show_text_report(
        "Copy Areas - Results",
        "\n".join(report_lines),
        ok_text="Close",
        cancel_text=None,
        width=780,
        height=620,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui = _load_uiutils()
        ui.uiUtils_alert(traceback.format_exc(), title="Copy Areas")
