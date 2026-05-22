import os

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
    "MakeKeyPlanView",
    doc=doc,
    legacy_sources=legacy_sources,
)

CONFIG_LAST_AREA_ID = "last_area_id"
CONFIG_LAST_KEYPLAN_TEMPLATE_ID = "last_keyplan_template_id"
CONFIG_LAST_FILL_TYPE_ID = "last_fill_type_id"


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


class KeyPlanOptionsDialog(object):
    def __init__(self, template_names, fill_type_names, area_label,
                 template_index=0, fill_type_index=0):
        xaml_path = os.path.join(script_dir, "KeyPlanWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = "Make Keyplans"

        helper = WindowInteropHelper(self.window)
        helper.Owner = uidoc.Application.MainWindowHandle

        self._lbl_area = self.window.FindName("AreaLabel")
        self._cmb_template = self.window.FindName("TemplateCombo")
        self._cmb_fill = self.window.FindName("FillTypeCombo")
        self._btn_ok = self.window.FindName("OkButton")
        self._btn_cancel = self.window.FindName("CancelButton")

        self.result = None

        self._btn_ok.Click += self._on_ok
        self._btn_cancel.Click += self._on_cancel

        self._lbl_area.Text = area_label or "(not selected)"

        for name in (template_names or []):
            self._cmb_template.Items.Add(name)
        for name in (fill_type_names or []):
            self._cmb_fill.Items.Add(name)

        if self._cmb_template.Items.Count > 0:
            self._cmb_template.SelectedIndex = min(
                template_index, self._cmb_template.Items.Count - 1)
        if self._cmb_fill.Items.Count > 0:
            self._cmb_fill.SelectedIndex = min(
                fill_type_index, self._cmb_fill.Items.Count - 1)

    def _on_ok(self, sender, args):
        self.result = {
            "template_index": self._cmb_template.SelectedIndex,
            "fill_type_index": self._cmb_fill.SelectedIndex,
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
        uiutils.uiUtils_alert("Active view must be a plan view.", title="Make Keyplans")
        return

    templates = [
        v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate
    ]
    templates_sorted = sorted(
        templates,
        key=lambda v: (get_element_name(v, "").lower(), element_id_value(v.Id) or 0),
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
        uiutils.uiUtils_alert("No view templates found for this view type.", title="Make Keyplans")
        return
    if not fill_types_sorted:
        uiutils.uiUtils_alert("No filled region types found.", title="Make Keyplans")
        return

    state = {
        "areas": [],
        "area_label": None,
        "keyplan_template_index": 0,
        "fill_type_index": 0,
    }

    last_area_id = getattr(config, CONFIG_LAST_AREA_ID, None)
    if last_area_id:
        area_elem = doc.GetElement(DB.ElementId(Int64(int(last_area_id))))
        if element_is_category(area_elem, DB.BuiltInCategory.OST_Areas):
            state["areas"] = [area_elem]
            state["area_label"] = get_element_name(area_elem) or _elem_id_int(area_elem.Id)

    last_keyplan_template_id = getattr(config, CONFIG_LAST_KEYPLAN_TEMPLATE_ID, None)
    if last_keyplan_template_id:
        for idx, template in enumerate(templates_sorted):
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
        use_last = uiutils.uiUtils_confirm("Use previously selected area(s)?", title="Make Keyplans")
        if not use_last:
            state["areas"] = []

    if not state["areas"]:
        try:
            areas = pick_elements(DB.BuiltInCategory.OST_Areas, "Select suite areas")
        except OperationCanceledException:
            return
        state["areas"] = areas

    areas = state.get("areas") or []
    if not areas:
        uiutils.uiUtils_alert("Please pick at least one area.", title="Make Keyplans")
        return

    if len(areas) > 1:
        state["area_label"] = "{} areas selected".format(len(areas))
    else:
        state["area_label"] = get_element_name(areas[0]) or _elem_id_int(areas[0].Id)

    dlg = KeyPlanOptionsDialog(
        [get_element_name(t, "<unnamed template>") for t in templates_sorted],
        [get_element_name(f, "<unnamed fill type>") for f in fill_types_sorted],
        area_label=state.get("area_label") or "(not selected)",
        template_index=state.get("keyplan_template_index", 0),
        fill_type_index=state.get("fill_type_index", 0),
    )
    result = dlg.show()
    if result is None:
        return

    template_index = result.get("template_index", 0)
    fill_type_index = result.get("fill_type_index", 0)

    keyplan_template = (
        templates_sorted[template_index]
        if 0 <= template_index < len(templates_sorted)
        else templates_sorted[0]
    )
    keyplan_fill_type = (
        fill_types_sorted[fill_type_index]
        if 0 <= fill_type_index < len(fill_types_sorted)
        else fill_types_sorted[0]
    )

    area_for_config = areas[0] if areas else None
    config.last_area_id = element_id_value(area_for_config.Id) if area_for_config else None
    config.last_keyplan_template_id = (
        element_id_value(keyplan_template.Id) if keyplan_template else None
    )
    config.last_fill_type_id = (
        element_id_value(keyplan_fill_type.Id) if keyplan_fill_type else None
    )
    save_config()

    results = []
    with revit.Transaction("Create Keyplan Views"):
        for area in areas:
            loop = get_outer_boundary_loop(area)
            fallback_loop = None
            if not loop:
                fallback_loop = _rect_loop_from_bbox(area.get_BoundingBox(base_view))
                loop = fallback_loop
            if not loop:
                results.append(
                    {
                        "area": area,
                        "keyplan_view": None,
                        "warnings": ["Could not create a filled region boundary."],
                        "failed": True,
                    }
                )
                continue

            base_label = area.Number or get_element_name(area) or _elem_id_int(str(area.Id))
            keyplan_view_id = base_view.Duplicate(DB.ViewDuplicateOption.Duplicate)
            keyplan_view = doc.GetElement(keyplan_view_id)
            keyplan_view.Name = unique_view_name("Keyplan - {}".format(base_label))
            keyplan_view.ViewTemplateId = keyplan_template.Id
            keyplan_view.CropBoxActive = True
            keyplan_view.CropBoxVisible = True

            create_errors = []
            fill_loops = List[DB.CurveLoop]()
            try:
                fill_loops.Add(loop)
                DB.FilledRegion.Create(doc, keyplan_fill_type.Id, keyplan_view.Id, fill_loops)
            except Exception:
                if fallback_loop is None:
                    fallback_loop = _rect_loop_from_bbox(area.get_BoundingBox(base_view))
                if fallback_loop:
                    try:
                        fill_loops = List[DB.CurveLoop]()
                        fill_loops.Add(fallback_loop)
                        DB.FilledRegion.Create(
                            doc, keyplan_fill_type.Id, keyplan_view.Id, fill_loops
                        )
                        create_errors.append(
                            "Filled region loop was discontinuous; used area bounding box."
                        )
                    except Exception:
                        create_errors.append("Filled region could not be created.")
                else:
                    create_errors.append("Filled region could not be created.")

            results.append(
                {
                    "area": area,
                    "keyplan_view": keyplan_view,
                    "warnings": create_errors,
                    "failed": False,
                }
            )

    last_keyplan = None
    for result in results:
        if result.get("keyplan_view"):
            last_keyplan = result.get("keyplan_view")

    if last_keyplan:
        try:
            uidoc.RequestViewChange(last_keyplan)
        except Exception:
            uidoc.ActiveView = last_keyplan

    created = [r for r in results if r.get("keyplan_view")]
    failed = [r for r in results if r.get("failed")]
    warnings = []
    for result in results:
        for warning in result.get("warnings") or []:
            warnings.append(warning)

    lines = ["Created {} keyplan view(s):".format(len(created))]
    for result in created:
        view = result.get("keyplan_view")
        area = result.get("area")
        lines.append("- {} ({})".format(get_element_name(view, "<unnamed view>"), _safe_elem_label(area)))
    if failed:
        lines.append("")
        lines.append("Failed {} area(s):".format(len(failed)))
        for result in failed:
            area = result.get("area")
            lines.append("- {}".format(_safe_elem_label(area)))
    message = "\n".join(lines)
    if warnings:
        message += "\n\nWarnings:\n" + "\n".join(sorted(set(warnings)))
    uiutils.uiUtils_alert(message, title="Keyplans Created")


if __name__ == "__main__":
    main()
