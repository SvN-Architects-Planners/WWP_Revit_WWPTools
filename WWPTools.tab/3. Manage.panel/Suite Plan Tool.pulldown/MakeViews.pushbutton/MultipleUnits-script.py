import os
import math
import random

from System import Int64
import clr
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import revit, DB, script
from WWP_settings import get_tool_settings
import WWP_uiUtils as uiutils
from Autodesk.Revit.Exceptions import OperationCanceledException
from Autodesk.Revit.UI import Selection as UISelection
from System.Collections.Generic import List
from System.IO import File
from System.Windows import Visibility
from System.Windows.Controls import SelectionChangedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader


def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-


script_dir = os.path.dirname(__file__)

doc = revit.doc
uidoc = revit.uidoc
legacy_sources = []
try:
    legacy_sources.append(script.get_config())
except Exception:
    pass
config, save_config = get_tool_settings(
    "MakeMarketingViews",
    doc=doc,
    legacy_sources=legacy_sources,
)

CONFIG_LAST_AREA_ID = "last_area_id"
CONFIG_LAST_DOOR_ID = "last_door_id"
CONFIG_LAST_TEMPLATE_ID = "last_template_id"
CONFIG_LAST_TITLEBLOCK_ID = "last_titleblock_id"
CONFIG_LAST_SHEET_NUMBER_PARAM = "last_sheet_number_param"
CONFIG_LAST_SHEET_NAME_PARAM = "last_sheet_name_param"
CONFIG_LAST_OVERWRITE = "last_overwrite_existing"
CONFIG_LAST_KEYPLAN_ENABLED = "last_keyplan_enabled"
CONFIG_LAST_KEYPLAN_TEMPLATE_ID = "last_keyplan_template_id"
CONFIG_LAST_FILL_TYPE_ID = "last_fill_type_id"


def pick_element(bic, prompt):
    bic_id = int(bic)
    view = doc.ActiveView
    isolate_active = False
    if bic_id == int(DB.BuiltInCategory.OST_Areas):
        try:
            view.IsolateCategoriesTemporary(List[DB.ElementId]([DB.ElementId(bic_id)]))
            isolate_active = True
        except Exception:
            isolate_active = False
    while True:
        try:
            ref = uidoc.Selection.PickObject(UISelection.ObjectType.Element, prompt)
            elem = doc.GetElement(ref)
            if elem and elem.Category and _elem_id_int(elem.Category.Id) == bic_id:
                return elem
            uiutils.uiUtils_alert("Selected element is not the expected category. Please try again.")
        finally:
            if isolate_active:
                try:
                    view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate)
                except Exception:
                    pass


def pick_elements(bic, prompt):
    bic_id = int(bic)
    view = doc.ActiveView
    isolate_active = False
    if bic_id == int(DB.BuiltInCategory.OST_Areas):
        try:
            view.IsolateCategoriesTemporary(List[DB.ElementId]([DB.ElementId(bic_id)]))
            isolate_active = True
        except Exception:
            isolate_active = False
    try:
        refs = uidoc.Selection.PickObjects(UISelection.ObjectType.Element, prompt)
        elems = []
        for ref in refs:
            elem = doc.GetElement(ref)
            if elem and elem.Category and _elem_id_int(elem.Category.Id) == bic_id:
                elems.append(elem)
        return elems
    finally:
        if isolate_active:
            try:
                view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate)
            except Exception:
                pass


def get_param_value(elem, name):
    if not name:
        return ""
    param = elem.LookupParameter(name)
    if not param:
        return ""
    if param.StorageType == DB.StorageType.String:
        return param.AsString() or ""
    if param.StorageType == DB.StorageType.Double:
        return param.AsValueString() or str(param.AsDouble())
    if param.StorageType == DB.StorageType.Integer:
        return str(param.AsInteger())
    if param.StorageType == DB.StorageType.ElementId:
        return param.AsValueString() or ""
    return ""


def curve_loop_area_xy(curves):
    pts = []
    for curve in curves:
        for pt in curve.Tessellate():
            if not pts or pt.DistanceTo(pts[-1]) > 1e-6:
                pts.append(pt)
    if len(pts) < 3:
        return 0.0
    if pts[0].DistanceTo(pts[-1]) > 1e-6:
        pts.append(pts[0])
    area = 0.0
    for i in range(len(pts) - 1):
        area += pts[i].X * pts[i + 1].Y - pts[i + 1].X * pts[i].Y
    return 0.5 * area


def get_outer_boundary_loop(area):
    opts = DB.SpatialElementBoundaryOptions()
    loops = area.GetBoundarySegments(opts)
    if not loops:
        return None
    best_curves = None
    best_area = -1.0
    for segs in loops:
        curves = [seg.GetCurve() for seg in segs]
        area_val = abs(curve_loop_area_xy(curves))
        if area_val > best_area:
            best_area = area_val
            best_curves = curves
    if not best_curves:
        return None
    loop = DB.CurveLoop()
    for curve in best_curves:
        try:
            loop.Append(curve)
        except Exception:
            return None
    return loop


def clear_scope_box(view):
    bip = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST_CROP", None)
    if bip:
        param = view.get_Parameter(bip)
        if param and param.AsElementId() != DB.ElementId.InvalidElementId:
            param.Set(DB.ElementId.InvalidElementId)
            return
    bip = getattr(DB.BuiltInParameter, "VIEWER_VOLUME_OF_INTEREST", None)
    if bip:
        param = view.get_Parameter(bip)
        if param and param.AsElementId() != DB.ElementId.InvalidElementId:
            param.Set(DB.ElementId.InvalidElementId)
            return
    param = view.LookupParameter("Scope Box")
    if param and param.AsElementId() != DB.ElementId.InvalidElementId:
        param.Set(DB.ElementId.InvalidElementId)


def rotate_view_to_door(view, door):
    facing = door.FacingOrientation
    vec = DB.XYZ(facing.X, facing.Y, 0.0)
    if vec.GetLength() < 1e-9:
        return False, "Door facing vector is zero."
    vec = vec.Normalize()

    y_axis = DB.XYZ.BasisY
    z_axis = DB.XYZ.BasisZ
    angle = math.atan2(y_axis.CrossProduct(vec).DotProduct(z_axis), y_axis.DotProduct(vec))
    if abs(angle) < 1e-9:
        return False, "Computed rotation angle is near zero."

    try:
        view.CropBoxVisible = True
    except Exception:
        pass
    try:
        doc.Regenerate()
    except Exception:
        pass

    crop_elem = None
    try:
        target_name = getattr(view, "Name", None)
        if target_name:
            elements = list(DB.FilteredElementCollector(doc, view.Id).ToElements())
            for element in elements:
                if getattr(element, "Name", None) == target_name:
                    crop_elem = element
                    break
    except Exception:
        crop_elem = None

    if crop_elem:
        axis = DB.Line.CreateUnbound(view.Origin, z_axis)
        DB.ElementTransformUtils.RotateElement(doc, crop_elem.Id, axis, angle)
        return True, "Rotated crop element {} by {:.2f} deg.".format(
            _elem_id_int(crop_elem.Id), math.degrees(angle)
        )

    angle_param = None
    bip = getattr(DB.BuiltInParameter, "VIEW_PLAN_ROTATION", None)
    if bip:
        angle_param = view.get_Parameter(bip)
    if not angle_param:
        angle_param = view.LookupParameter("View Rotation")
    if angle_param and not angle_param.IsReadOnly:
        angle_param.Set(angle_param.AsDouble() + angle)
        return True, "Rotated view parameter by {:.2f} deg.".format(math.degrees(angle))

    reason = "No crop element found and view rotation parameter is unavailable or read-only."
    if angle_param and angle_param.IsReadOnly:
        reason = "View rotation parameter is read-only and no crop element found."
    return False, reason


def unique_view_name(base_name):
    existing = set(get_element_name(v) for v in DB.FilteredElementCollector(doc).OfClass(DB.View))
    if base_name not in existing:
        return base_name
    index = 1
    while True:
        candidate = "{} ({})".format(base_name, index)
        if candidate not in existing:
            return candidate
        index += 1


def unique_sheet_number(base_number):
    if not base_number:
        return ""
    existing = set(
        s.SheetNumber
        for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet)
        if s.SheetNumber
    )
    if base_number not in existing:
        return base_number
    index = 1
    while True:
        candidate = "{}-copy{}".format(base_number, "" if index == 1 else str(index))
        if candidate not in existing:
            return candidate
        index += 1


def element_id_value(elem_id):
    if elem_id is None:
        return None
    if hasattr(elem_id, "IntegerValue"):
        return _elem_id_int(elem_id)
    if hasattr(elem_id, "Value"):
        return elem_id.Value
    try:
        return int(elem_id)
    except Exception:
        return None


def element_is_category(elem, bic):
    if not elem or not elem.Category:
        return False
    return _elem_id_int(elem.Category.Id) == int(bic)


def get_parameter_names(elem):
    names = set()
    for param in elem.Parameters:
        if param.Definition and param.Definition.Name:
            names.add(param.Definition.Name)
    return sorted(names)


def _safe_elem_label(elem):
    try:
        return get_element_name(elem) or _elem_id_int(elem.Id)
    except Exception:
        return "(unavailable)"


def get_element_name(elem, default=""):
    if elem is None:
        return default
    try:
        return elem.Name or default
    except Exception:
        pass
    try:
        return DB.Element.Name.__get__(elem) or default
    except Exception:
        return default


def _report_sheet_number(sheet):
    try:
        number = sheet.SheetNumber
        if number:
            return number
    except Exception:
        pass
    return "MARKETING&{}".format(random.randint(1000, 9999))

def _door_name_matches(door):
    def _param_string(param):
        try:
            if param is None:
                return ""
            if param.StorageType == DB.StorageType.String:
                return param.AsString() or ""
            return param.AsValueString() or ""
        except Exception:
            return ""

    name = get_element_name(door, "")
    symbol = None
    try:
        symbol = door.Symbol
    except Exception:
        symbol = None
    parts = [name]
    try:
        parts.append(_param_string(door.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)))
    except Exception:
        pass
    if symbol:
        try:
            parts.append(get_element_name(symbol, ""))
        except Exception:
            pass
        try:
            parts.append(symbol.FamilyName or "")
        except Exception:
            pass
    joined = " ".join([p for p in parts if p]).lower()
    return "entry" in joined


def _door_location_point(door):
    try:
        loc = door.Location
        if isinstance(loc, DB.LocationPoint):
            return loc.Point
        if isinstance(loc, DB.LocationCurve):
            curve = loc.Curve
            if curve:
                return curve.Evaluate(0.5, True)
    except Exception:
        return None
    return None


def _door_points_for_check(door):
    points = []
    try:
        loc = door.Location
        if isinstance(loc, DB.LocationPoint):
            points.append(loc.Point)
        elif isinstance(loc, DB.LocationCurve):
            curve = loc.Curve
            if curve:
                points.append(curve.GetEndPoint(0))
                points.append(curve.GetEndPoint(1))
                points.append(curve.Evaluate(0.5, True))
    except Exception:
        return points

    try:
        facing = door.FacingOrientation
        if facing and not facing.IsZeroLength():
            facing = facing.Normalize()
            offset = facing.Multiply(1.0)
            points.extend([pt + offset for pt in points])
            points.extend([pt - offset for pt in points])
    except Exception:
        pass

    return points


def _centroid_from_loop(loop):
    try:
        pts = []
        for curve in loop:
            for pt in curve.Tessellate():
                if not pts or pt.DistanceTo(pts[-1]) > 1e-6:
                    pts.append(pt)
        if len(pts) < 3:
            return None
        if pts[0].DistanceTo(pts[-1]) > 1e-6:
            pts.append(pts[0])
        area = 0.0
        cx = 0.0
        cy = 0.0
        for i in range(len(pts) - 1):
            cross = pts[i].X * pts[i + 1].Y - pts[i + 1].X * pts[i].Y
            area += cross
            cx += (pts[i].X + pts[i + 1].X) * cross
            cy += (pts[i].Y + pts[i + 1].Y) * cross
        if abs(area) < 1e-9:
            return None
        area *= 0.5
        cx = cx / (6.0 * area)
        cy = cy / (6.0 * area)
        return DB.XYZ(cx, cy, pts[0].Z)
    except Exception:
        return None


def _area_centroid(area):
    try:
        loop = get_outer_boundary_loop(area)
        if loop:
            return _centroid_from_loop(loop)
    except Exception:
        return None
    return None


def _rect_loop_from_bbox(bbox):
    if not bbox:
        return None
    min_pt = bbox.Min
    max_pt = bbox.Max
    if not min_pt or not max_pt:
        return None
    p1 = DB.XYZ(min_pt.X, min_pt.Y, min_pt.Z)
    p2 = DB.XYZ(max_pt.X, min_pt.Y, min_pt.Z)
    p3 = DB.XYZ(max_pt.X, max_pt.Y, min_pt.Z)
    p4 = DB.XYZ(min_pt.X, max_pt.Y, min_pt.Z)
    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(p1, p2))
    loop.Append(DB.Line.CreateBound(p2, p3))
    loop.Append(DB.Line.CreateBound(p3, p4))
    loop.Append(DB.Line.CreateBound(p4, p1))
    return loop


def _get_titleblock_bounds(sheet):
    titleblocks = (
        DB.FilteredElementCollector(doc, sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for titleblock in titleblocks:
        try:
            bbox = titleblock.get_BoundingBox(sheet) or titleblock.get_BoundingBox(None)
            if bbox:
                return bbox.Min, bbox.Max
        except Exception:
            continue
    outline = sheet.Outline
    return (
        DB.XYZ(outline.Min.U, outline.Min.V, 0.0),
        DB.XYZ(outline.Max.U, outline.Max.V, 0.0),
    )


def _clamp_viewport_center(viewport, target_center, bounds_min, bounds_max):
    try:
        outline = viewport.GetBoxOutline()
    except Exception:
        viewport.SetBoxCenter(DB.XYZ(target_center.X, target_center.Y, 0.0))
        return

    outline_min = getattr(outline, "Min", None) or getattr(outline, "MinimumPoint", None)
    outline_max = getattr(outline, "Max", None) or getattr(outline, "MaximumPoint", None)
    if outline_min is None or outline_max is None:
        outline_min = getattr(outline, "Minimum", None)
        outline_max = getattr(outline, "Maximum", None)
    if outline_min is None or outline_max is None:
        viewport.SetBoxCenter(DB.XYZ(target_center.X, target_center.Y, 0.0))
        return

    half_w = (outline_max.X - outline_min.X) / 2.0
    half_h = (outline_max.Y - outline_min.Y) / 2.0
    available_w = bounds_max.X - bounds_min.X
    available_h = bounds_max.Y - bounds_min.Y

    if available_w < 2.0 * half_w:
        center_x = (bounds_min.X + bounds_max.X) / 2.0
    else:
        center_x = max(bounds_min.X + half_w, min(target_center.X, bounds_max.X - half_w))

    if available_h < 2.0 * half_h:
        center_y = (bounds_min.Y + bounds_max.Y) / 2.0
    else:
        center_y = max(bounds_min.Y + half_h, min(target_center.Y, bounds_max.Y - half_h))

    viewport.SetBoxCenter(DB.XYZ(center_x, center_y, 0.0))


def _viewport_half_size(viewport):
    try:
        outline = viewport.GetBoxOutline()
    except Exception:
        return None

    outline_min = getattr(outline, "Min", None) or getattr(outline, "MinimumPoint", None)
    outline_max = getattr(outline, "Max", None) or getattr(outline, "MaximumPoint", None)
    if outline_min is None or outline_max is None:
        outline_min = getattr(outline, "Minimum", None)
        outline_max = getattr(outline, "Maximum", None)
    if outline_min is None or outline_max is None:
        return None
    return (outline_max.X - outline_min.X) / 2.0, (outline_max.Y - outline_min.Y) / 2.0


def find_entry_door_in_area(area, view):
    if area is None:
        return None

    try:
        collector = DB.FilteredElementCollector(doc, view.Id)
        doors = (
            collector.OfCategory(DB.BuiltInCategory.OST_Doors)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        doors = []

    matches = []
    for door in doors:
        if not _door_name_matches(door):
            continue
        points = _door_points_for_check(door)
        if not points:
            continue
        for pt in points:
            try:
                if area.IsPointInArea(pt):
                    matches.append(door)
                    break
            except Exception:
                continue

    if not matches:
        centroid = _area_centroid(area)
        if centroid:
            candidates = [d for d in doors if _door_name_matches(d)]
            if candidates:
                candidates.sort(
                    key=lambda d: _door_location_point(d).DistanceTo(centroid)
                    if _door_location_point(d)
                    else float("inf")
                )
                return candidates[0]
        return None
    matches.sort(key=lambda d: _elem_id_int(d.Id))
    return matches[0]


class MarketingViewOptionsDialog(object):
    def __init__(self,
                 sheet_params, template_names, titleblock_names,
                 keyplan_template_names, fill_type_names,
                 area_label="(not selected)", door_label="",
                 keyplan_enabled=False, overwrite_existing=False,
                 template_index=0, titleblock_index=0,
                 keyplan_template_index=0, fill_type_index=0,
                 sheet_number_param="", sheet_name_param=""):
        xaml_path = os.path.join(script_dir, "MarketingViewWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = "Make Marketing View"

        helper = WindowInteropHelper(self.window)
        helper.Owner = uidoc.Application.MainWindowHandle

        self._lbl_area = self.window.FindName("AreaLabel")
        self._lbl_door = self.window.FindName("DoorLabel")
        self._cmb_sheet_number = self.window.FindName("SheetNumberParamCombo")
        self._cmb_sheet_name = self.window.FindName("SheetNameParamCombo")
        self._cmb_template = self.window.FindName("TemplateCombo")
        self._cmb_titleblock = self.window.FindName("TitleblockCombo")
        self._chk_overwrite = self.window.FindName("OverwriteCheckBox")
        self._chk_keyplan = self.window.FindName("KeyplanCheckBox")
        self._cmb_keyplan_template = self.window.FindName("KeyplanTemplateCombo")
        self._cmb_fill_type = self.window.FindName("FillTypeCombo")
        self._btn_ok = self.window.FindName("OkButton")
        self._btn_cancel = self.window.FindName("CancelButton")

        self.result = None

        self._btn_ok.Click += self._on_ok
        self._btn_cancel.Click += self._on_cancel
        self._chk_keyplan.Checked += self._on_keyplan_changed
        self._chk_keyplan.Unchecked += self._on_keyplan_changed

        # Populate labels
        self._lbl_area.Text = area_label or "(not selected)"
        self._lbl_door.Text = door_label or "(not found)"

        # Populate combo boxes
        for name in (sheet_params or []):
            self._cmb_sheet_number.Items.Add(name)
            self._cmb_sheet_name.Items.Add(name)
        for name in (template_names or []):
            self._cmb_template.Items.Add(name)
        for name in (titleblock_names or []):
            self._cmb_titleblock.Items.Add(name)
        for name in (keyplan_template_names or []):
            self._cmb_keyplan_template.Items.Add(name)
        for name in (fill_type_names or []):
            self._cmb_fill_type.Items.Add(name)

        # Set selected items by value match, fallback to index 0
        def _find_index(combo, value):
            if value:
                for i in range(combo.Items.Count):
                    if str(combo.Items[i]) == str(value):
                        return i
            return 0

        if self._cmb_sheet_number.Items.Count > 0:
            self._cmb_sheet_number.SelectedIndex = _find_index(
                self._cmb_sheet_number, sheet_number_param)
        if self._cmb_sheet_name.Items.Count > 0:
            self._cmb_sheet_name.SelectedIndex = _find_index(
                self._cmb_sheet_name, sheet_name_param)
        if self._cmb_template.Items.Count > 0:
            self._cmb_template.SelectedIndex = min(
                template_index, self._cmb_template.Items.Count - 1)
        if self._cmb_titleblock.Items.Count > 0:
            self._cmb_titleblock.SelectedIndex = min(
                titleblock_index, self._cmb_titleblock.Items.Count - 1)
        if self._cmb_keyplan_template.Items.Count > 0:
            self._cmb_keyplan_template.SelectedIndex = min(
                keyplan_template_index, self._cmb_keyplan_template.Items.Count - 1)
        if self._cmb_fill_type.Items.Count > 0:
            self._cmb_fill_type.SelectedIndex = min(
                fill_type_index, self._cmb_fill_type.Items.Count - 1)

        self._chk_overwrite.IsChecked = bool(overwrite_existing)
        self._chk_keyplan.IsChecked = bool(keyplan_enabled)
        self._update_keyplan_enabled(bool(keyplan_enabled))

    def _update_keyplan_enabled(self, enabled):
        self._cmb_keyplan_template.IsEnabled = enabled
        self._cmb_fill_type.IsEnabled = enabled

    def _on_keyplan_changed(self, sender, args):
        self._update_keyplan_enabled(bool(self._chk_keyplan.IsChecked))

    def _on_ok(self, sender, args):
        sheet_num_item = self._cmb_sheet_number.SelectedItem
        sheet_name_item = self._cmb_sheet_name.SelectedItem
        self.result = {
            "sheet_number_param": str(sheet_num_item) if sheet_num_item is not None else "",
            "sheet_name_param": str(sheet_name_item) if sheet_name_item is not None else "",
            "template_index": max(0, self._cmb_template.SelectedIndex),
            "titleblock_index": max(0, self._cmb_titleblock.SelectedIndex),
            "keyplan_enabled": bool(self._chk_keyplan.IsChecked),
            "keyplan_template_index": max(0, self._cmb_keyplan_template.SelectedIndex),
            "fill_type_index": max(0, self._cmb_fill_type.SelectedIndex),
            "overwrite_existing": bool(self._chk_overwrite.IsChecked),
        }
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.result = None
        self.window.Close()

    def show(self):
        self.window.ShowDialog()
        return self.result


def main():
    base_view = doc.ActiveView
    if not isinstance(base_view, DB.ViewPlan):
        uiutils.uiUtils_alert("Active view must be a plan view.", title="Make Marketing View")
        return

    templates = [
        v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate
    ]
    templates_sorted = sorted(
        templates,
        key=lambda v: (get_element_name(v, "").lower(), element_id_value(v.Id) or 0),
    )
    keyplan_templates_sorted = list(templates_sorted)

    titleblocks = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements()
    )
    titleblocks_sorted = sorted(
        titleblocks,
        key=lambda t: (
            (getattr(t, "FamilyName", "") or "").lower(),
            get_element_name(t, "").lower(),
            element_id_value(t.Id) or 0,
        ),
    )

    fill_types = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FilledRegionType)
        .WhereElementIsElementType()
        .ToElements()
    )
    fill_types_sorted = sorted(
        fill_types,
        key=lambda f: (get_element_name(f, "").lower(), element_id_value(f.Id) or 0),
    )

    if not templates_sorted:
        uiutils.uiUtils_alert("No view templates found for this view type.", title="Make Marketing View")
        return
    if not fill_types_sorted:
        uiutils.uiUtils_alert("No filled region types found.", title="Make Marketing View")
        return
    if not titleblocks_sorted:
        uiutils.uiUtils_alert("No titleblock types found.", title="Make Marketing View")
        return

    state = {
        "area": None,
        "areas": [],
        "door": None,
        "area_label": None,
        "door_label": None,
        "sheet_number_param": getattr(config, CONFIG_LAST_SHEET_NUMBER_PARAM, None),
        "sheet_name_param": getattr(config, CONFIG_LAST_SHEET_NAME_PARAM, None),
        "overwrite_existing": bool(getattr(config, CONFIG_LAST_OVERWRITE, False)),
        "keyplan_enabled": bool(getattr(config, CONFIG_LAST_KEYPLAN_ENABLED, False)),
        "template_index": 0,
        "titleblock_index": 0,
        "keyplan_template_index": 0,
        "fill_type_index": 0,
    }

    last_area_id = getattr(config, CONFIG_LAST_AREA_ID, None)
    if last_area_id:
        area_elem = doc.GetElement(DB.ElementId(Int64(int(last_area_id))))
        if element_is_category(area_elem, DB.BuiltInCategory.OST_Areas):
            state["area"] = area_elem
            state["areas"] = [area_elem]
            state["area_label"] = get_element_name(area_elem) or _elem_id_int(area_elem.Id)

    last_door_id = getattr(config, CONFIG_LAST_DOOR_ID, None)
    if last_door_id:
        door_elem = doc.GetElement(DB.ElementId(Int64(int(last_door_id))))
        if element_is_category(door_elem, DB.BuiltInCategory.OST_Doors):
            state["door"] = door_elem
            state["door_label"] = get_element_name(door_elem) or _elem_id_int(door_elem.Id)

    last_template_id = getattr(config, CONFIG_LAST_TEMPLATE_ID, None)
    if last_template_id:
        for idx, template in enumerate(templates_sorted):
            if _elem_id_int(template.Id) == int(last_template_id):
                state["template_index"] = idx
                break

    last_titleblock_id = getattr(config, CONFIG_LAST_TITLEBLOCK_ID, None)
    if last_titleblock_id:
        for idx, titleblock in enumerate(titleblocks_sorted):
            if _elem_id_int(titleblock.Id) == int(last_titleblock_id):
                state["titleblock_index"] = idx
                break

    last_keyplan_template_id = getattr(config, CONFIG_LAST_KEYPLAN_TEMPLATE_ID, None)
    if last_keyplan_template_id:
        for idx, template in enumerate(keyplan_templates_sorted):
            if _elem_id_int(template.Id) == int(last_keyplan_template_id):
                state["keyplan_template_index"] = idx
                break

    last_fill_type_id = getattr(config, CONFIG_LAST_FILL_TYPE_ID, None)
    if last_fill_type_id:
        for idx, fill_type in enumerate(fill_types_sorted):
            if _elem_id_int(fill_type.Id) == int(last_fill_type_id):
                state["fill_type_index"] = idx
                break

    if state["areas"]:
        use_last = uiutils.uiUtils_confirm(
            "Use previously selected area(s)?", title="Make Marketing View"
        )
        if not use_last:
            state["areas"] = []
            state["area"] = None

    if not state["areas"]:
        try:
            areas = pick_elements(DB.BuiltInCategory.OST_Areas, "Select suite areas")
        except OperationCanceledException:
            return
        state["areas"] = areas

    areas = state.get("areas") or []
    if not areas:
        uiutils.uiUtils_alert("Please pick at least one area.", title="Make Marketing View")
        return

    area = areas[0]
    state["area"] = area
    state["area_label"] = (
        "{} areas selected".format(len(areas))
        if len(areas) > 1
        else (get_element_name(area) or _elem_id_int(area.Id))
    )
    door = find_entry_door_in_area(area, base_view) if area else None
    state["door"] = door
    state["door_label"] = get_element_name(door) if door else "(not selected)"

    sheet_param_label = "(Use Area Number/Name)"
    sheet_params = [sheet_param_label]
    if area:
        for name in get_parameter_names(area):
            if name not in sheet_params:
                sheet_params.append(name)

    sheet_number_param = state.get("sheet_number_param") or ""
    sheet_name_param = state.get("sheet_name_param") or ""
    if sheet_number_param and sheet_number_param not in sheet_params:
        sheet_params.append(sheet_number_param)
    if sheet_name_param and sheet_name_param not in sheet_params:
        sheet_params.append(sheet_name_param)

    dlg = MarketingViewOptionsDialog(
        sheet_params,
        [get_element_name(t, "<unnamed template>") for t in templates_sorted],
        ["{}: {}".format(getattr(t, "FamilyName", "") or "", get_element_name(t, "<unnamed titleblock>")) for t in titleblocks_sorted],
        [get_element_name(t, "<unnamed template>") for t in keyplan_templates_sorted],
        [get_element_name(t, "<unnamed fill type>") for t in fill_types_sorted],
        area_label=state.get("area_label") or "(not selected)",
        door_label=state.get("door_label") or "",
        keyplan_enabled=state.get("keyplan_enabled", False),
        overwrite_existing=state.get("overwrite_existing", False),
        template_index=state.get("template_index", 0),
        titleblock_index=state.get("titleblock_index", 0),
        keyplan_template_index=state.get("keyplan_template_index", 0),
        fill_type_index=state.get("fill_type_index", 0),
        sheet_number_param=sheet_number_param or sheet_param_label,
        sheet_name_param=sheet_name_param or sheet_param_label,
    )
    result = dlg.show()
    if result is None:
        return

    sheet_number_param = result.get("sheet_number_param") or ""
    sheet_name_param = result.get("sheet_name_param") or ""
    if sheet_number_param == sheet_param_label:
        sheet_number_param = ""
    if sheet_name_param == sheet_param_label:
        sheet_name_param = ""

    template_index = result.get("template_index", 0)
    titleblock_index = result.get("titleblock_index", 0)
    keyplan_template_index = result.get("keyplan_template_index", 0)
    fill_type_index = result.get("fill_type_index", 0)
    overwrite_existing = bool(result.get("overwrite_existing"))
    create_keyplan = bool(result.get("keyplan_enabled"))

    view_template = (
        templates_sorted[template_index]
        if 0 <= template_index < len(templates_sorted)
        else templates_sorted[0]
    )
    titleblock_type = (
        titleblocks_sorted[titleblock_index]
        if 0 <= titleblock_index < len(titleblocks_sorted)
        else titleblocks_sorted[0]
    )
    keyplan_template = (
        keyplan_templates_sorted[keyplan_template_index]
        if 0 <= keyplan_template_index < len(keyplan_templates_sorted)
        else None
    )
    keyplan_fill_type = (
        fill_types_sorted[fill_type_index]
        if 0 <= fill_type_index < len(fill_types_sorted)
        else None
    )

    area_for_config = areas[0] if areas else None
    door_for_config = find_entry_door_in_area(area_for_config, base_view) if area_for_config else None
    config.last_area_id = element_id_value(area_for_config.Id) if area_for_config else None
    config.last_door_id = element_id_value(door_for_config.Id) if door_for_config else None
    config.last_template_id = element_id_value(view_template.Id) if view_template else None
    config.last_titleblock_id = element_id_value(titleblock_type.Id) if titleblock_type else None
    config.last_keyplan_enabled = bool(create_keyplan)
    config.last_keyplan_template_id = (
        element_id_value(keyplan_template.Id) if keyplan_template else None
    )
    config.last_fill_type_id = element_id_value(keyplan_fill_type.Id) if keyplan_fill_type else None
    config.last_sheet_number_param = sheet_number_param or ""
    config.last_sheet_name_param = sheet_name_param or ""
    config.last_overwrite_existing = bool(overwrite_existing)
    save_config()

    results = []
    with revit.Transaction("Make Marketing View"):
        for area in areas:
            sheet_number = get_param_value(area, sheet_number_param)
            if not sheet_number:
                sheet_number = getattr(area, "Number", "") or ""
            sheet_name = get_param_value(area, sheet_name_param)
            if not sheet_name:
                sheet_name = get_element_name(area) or "Marketing Sheet"

            base_label = sheet_name or get_element_name(area) or getattr(area, "Number", "") or "Marketing"
            view_name_base = "Marketing - {}".format(base_label)
            view_name = unique_view_name(view_name_base)

            existing_sheet = None
            if sheet_number:
                for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
                    if sheet.SheetNumber == sheet_number:
                        existing_sheet = sheet
                        break

            if existing_sheet and not overwrite_existing:
                sheet_number = unique_sheet_number(sheet_number)
                view_name = unique_view_name("{} - copy".format(view_name_base))

            loop = get_outer_boundary_loop(area)
            if not loop:
                results.append(
                    {
                        "area": area,
                        "warnings": ["Could not get a closed boundary loop from the selected area."],
                        "failed": True,
                    }
                )
                continue

            door = find_entry_door_in_area(area, base_view)
            create_errors = []

            if overwrite_existing and existing_sheet:
                doc.Delete(existing_sheet.Id)
                existing_sheet = None
            new_view_id = base_view.Duplicate(DB.ViewDuplicateOption.Duplicate)
            marketing_view = doc.GetElement(new_view_id)
            marketing_view.Name = view_name
            marketing_view.ViewTemplateId = view_template.Id

            clear_scope_box(marketing_view)
            marketing_view.CropBoxActive = True
            marketing_view.CropBoxVisible = True
            if door:
                rotated, rotate_msg = rotate_view_to_door(marketing_view, door)
            else:
                rotated, rotate_msg = False, "No entry door found for this area."

            crop_mgr = marketing_view.GetCropRegionShapeManager()
            try:
                crop_mgr.SetCropShape(loop)
            except Exception:
                fallback_loop = _rect_loop_from_bbox(area.get_BoundingBox(base_view))
                if fallback_loop:
                    try:
                        crop_mgr.SetCropShape(fallback_loop)
                        create_errors.append(
                            "Area crop loop was discontinuous; used area bounding box instead."
                        )
                    except Exception:
                        create_errors.append("Area crop loop was discontinuous; crop not set.")
                else:
                    create_errors.append("Area crop loop was discontinuous; crop not set.")

            keyplan_view = None
            if create_keyplan and keyplan_template and keyplan_fill_type:
                keyplan_view_id = base_view.Duplicate(DB.ViewDuplicateOption.Duplicate)
                keyplan_view = doc.GetElement(keyplan_view_id)
                keyplan_view.Name = unique_view_name("Keyplan - {}".format(base_label))
                keyplan_view.ViewTemplateId = keyplan_template.Id
                keyplan_view.CropBoxActive = True
                keyplan_view.CropBoxVisible = True
                fill_loops = List[DB.CurveLoop]()
                try:
                    fill_loops.Add(loop)
                    DB.FilledRegion.Create(doc, keyplan_fill_type.Id, keyplan_view.Id, fill_loops)
                except Exception:
                    fallback_loop = _rect_loop_from_bbox(area.get_BoundingBox(base_view))
                    if fallback_loop:
                        try:
                            fill_loops = List[DB.CurveLoop]()
                            fill_loops.Add(fallback_loop)
                            DB.FilledRegion.Create(
                                doc, keyplan_fill_type.Id, keyplan_view.Id, fill_loops
                            )
                            create_errors.append(
                                "Keyplan fill loop was discontinuous; used area bounding box."
                            )
                        except Exception:
                            create_errors.append("Keyplan fill loop was discontinuous; fill not created.")
                    else:
                        create_errors.append("Keyplan fill loop was discontinuous; fill not created.")

            sheet = DB.ViewSheet.Create(doc, titleblock_type.Id)
            if sheet_number:
                try:
                    sheet.SheetNumber = sheet_number
                except Exception:
                    create_errors.append("Sheet number '{}' could not be set.".format(sheet_number))
            if sheet_name:
                try:
                    sheet.Name = sheet_name
                except Exception:
                    create_errors.append("Sheet name '{}' could not be set.".format(sheet_name))

            if DB.Viewport.CanAddViewToSheet(doc, sheet.Id, marketing_view.Id):
                bounds_min, bounds_max = _get_titleblock_bounds(sheet)
                center = DB.XYZ(
                    (bounds_min.X + bounds_max.X) / 2.0,
                    (bounds_min.Y + bounds_max.Y) / 2.0,
                    0.0,
                )
                offset = (bounds_max.X - bounds_min.X) * 0.25 if create_keyplan else 0.0
                marketing_pt = DB.XYZ(center.X - offset, center.Y, 0.0)
                marketing_vp = DB.Viewport.Create(doc, sheet.Id, marketing_view.Id, marketing_pt)
                if isinstance(marketing_vp, DB.ElementId):
                    marketing_vp = doc.GetElement(marketing_vp)
                if marketing_vp:
                    doc.Regenerate()
                    if create_keyplan and keyplan_view:
                        keyplan_pt = DB.XYZ(center.X + offset, center.Y, 0.0)
                        if DB.Viewport.CanAddViewToSheet(doc, sheet.Id, keyplan_view.Id):
                            keyplan_vp = DB.Viewport.Create(
                                doc, sheet.Id, keyplan_view.Id, keyplan_pt
                            )
                            if isinstance(keyplan_vp, DB.ElementId):
                                keyplan_vp = doc.GetElement(keyplan_vp)
                            if keyplan_vp:
                                doc.Regenerate()
                                margin = (bounds_max.X - bounds_min.X) * 0.02
                                marketing_half = _viewport_half_size(marketing_vp)
                                keyplan_half = _viewport_half_size(keyplan_vp)
                                if marketing_half and keyplan_half:
                                    marketing_target = DB.XYZ(
                                        bounds_min.X + marketing_half[0] + margin,
                                        center.Y,
                                        0.0,
                                    )
                                    keyplan_target = DB.XYZ(
                                        bounds_max.X - keyplan_half[0] - margin,
                                        center.Y,
                                        0.0,
                                    )
                                    _clamp_viewport_center(
                                        marketing_vp, marketing_target, bounds_min, bounds_max
                                    )
                                    _clamp_viewport_center(
                                        keyplan_vp, keyplan_target, bounds_min, bounds_max
                                    )
                                else:
                                    _clamp_viewport_center(
                                        marketing_vp, marketing_pt, bounds_min, bounds_max
                                    )
                                    _clamp_viewport_center(
                                        keyplan_vp, keyplan_pt, bounds_min, bounds_max
                                    )
                        else:
                            _clamp_viewport_center(
                                marketing_vp, marketing_pt, bounds_min, bounds_max
                            )
                            keyplan_vp = None
                    else:
                        _clamp_viewport_center(marketing_vp, marketing_pt, bounds_min, bounds_max)

            if not rotated:
                create_errors.append("Rotation failed: {}".format(rotate_msg))

            results.append(
                {
                    "area": area,
                    "sheet": sheet,
                    "marketing_view": marketing_view,
                    "keyplan_view": keyplan_view,
                    "warnings": create_errors,
                    "failed": False,
                }
            )

    last_sheet = None
    last_marketing = None
    last_keyplan = None
    for result in results:
        if result.get("sheet"):
            last_sheet = result.get("sheet")
            last_marketing = result.get("marketing_view")
            last_keyplan = result.get("keyplan_view")

    try:
        if last_marketing:
            try:
                uidoc.RequestViewChange(last_marketing)
            except Exception:
                uidoc.ActiveView = last_marketing
        if last_keyplan:
            try:
                uidoc.RequestViewChange(last_keyplan)
            except Exception:
                uidoc.ActiveView = last_keyplan
        if last_sheet:
            try:
                uidoc.RequestViewChange(last_sheet)
                uidoc.ActiveView = last_sheet
                uidoc.RefreshActiveView()
            except Exception:
                uidoc.ActiveView = last_sheet
    except Exception:
        pass

    created = [r for r in results if r.get("sheet")]
    failed = [r for r in results if r.get("failed")]
    warnings = []
    for result in results:
        for warning in result.get("warnings") or []:
            warnings.append(warning)

    lines = ["Created {} sheet(s):".format(len(created))]
    for result in created:
        sheet = result.get("sheet")
        area = result.get("area")
        sheet_number = _report_sheet_number(sheet)
        lines.append("- {} ({})".format(sheet_number, _safe_elem_label(area)))
    if failed:
        lines.append("")
        lines.append("Failed {} area(s):".format(len(failed)))
        for result in failed:
            area = result.get("area")
            lines.append("- {}".format(_safe_elem_label(area)))
    message = "\n".join(lines)
    if warnings:
        message += "\n\nWarnings:\n" + "\n".join(sorted(set(warnings)))
    uiutils.uiUtils_alert(message, title="Marketing Sheets Created")


if __name__ == "__main__":
    main()
