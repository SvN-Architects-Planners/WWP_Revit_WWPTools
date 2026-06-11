# -*- coding: utf-8 -*-
import os
import sys
import traceback

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit import DB, UI
from System.Collections.Generic import List

TITLE = "Mass to In-Place"

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui


def _elem_id_int(eid):
    try:
        return int(eid.Value)
    except AttributeError:
        return int(eid.IntegerValue)


def _get_uidoc():
    try:
        return __revit__.ActiveUIDocument
    except Exception:
        return None


def _get_doc():
    uidoc = _get_uidoc()
    return uidoc.Document if uidoc is not None else None


def _is_mass_family_instance(element):
    if not isinstance(element, DB.FamilyInstance):
        return False
    try:
        cat = element.Category
        if cat is None:
            return False
        return _elem_id_int(cat.Id) == int(DB.BuiltInCategory.OST_Mass)
    except Exception:
        return False


def _instance_display_name(element):
    try:
        symbol = element.Symbol
        if symbol is not None:
            family = symbol.Family
            if family is not None:
                return "{} : {}".format(family.Name or "", symbol.Name or "")
            return symbol.Name or ""
    except Exception:
        pass
    try:
        return element.Name or ""
    except Exception:
        return "Element_{}".format(_elem_id_int(element.Id))


def _iter_solids(geometry_element):
    solids = []
    if geometry_element is None:
        return solids
    for geo_obj in geometry_element:
        if isinstance(geo_obj, DB.Solid):
            try:
                if geo_obj.Volume > 1e-9 and geo_obj.Faces.Size > 0:
                    solids.append(geo_obj)
            except Exception:
                pass
        elif isinstance(geo_obj, DB.GeometryInstance):
            try:
                # GetInstanceGeometry() returns geometry in project (world) coordinates
                solids.extend(_iter_solids(geo_obj.GetInstanceGeometry()))
            except Exception:
                pass
        elif isinstance(geo_obj, DB.GeometryElement):
            solids.extend(_iter_solids(geo_obj))
    return solids


def _get_instance_solids(element):
    options = DB.Options()
    try:
        options.IncludeNonVisibleObjects = True
    except Exception:
        pass
    geometry = element.get_Geometry(options)
    solids = _iter_solids(geometry)
    solids.sort(key=lambda s: s.Volume, reverse=True)
    # Deduplicate near-identical volumes (geometry instanced multiple times)
    seen_volumes = []
    unique = []
    for solid in solids:
        vol = solid.Volume
        if not any(abs(vol - v) < 1e-6 for v in seen_volumes):
            unique.append(solid)
            seen_volumes.append(vol)
    return unique


def _set_comment(element, text):
    try:
        param = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if param and not param.IsReadOnly:
            param.Set(text)
            return
    except Exception:
        pass
    try:
        param = element.LookupParameter("Comments")
        if param and not param.IsReadOnly:
            param.Set(text)
    except Exception:
        pass


def _collect_targets(doc, uidoc):
    selected = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            el = doc.GetElement(eid)
            if _is_mass_family_instance(el):
                selected.append(el)
    except Exception:
        pass
    if selected:
        return selected, "Current selection ({} instance(s))".format(len(selected))

    all_instances = []
    try:
        collector = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.FamilyInstance)
            .OfCategory(DB.BuiltInCategory.OST_Mass)
            .WhereElementIsNotElementType()
        )
        for el in collector:
            if _is_mass_family_instance(el):
                all_instances.append(el)
    except Exception:
        pass
    return all_instances, "All mass family instances in project"


def main():
    uidoc = _get_uidoc()
    doc = _get_doc()
    if uidoc is None or doc is None:
        UI.TaskDialog.Show(TITLE, "No active Revit document found.")
        return

    elements, scope_label = _collect_targets(doc, uidoc)
    if not elements:
        ui.uiUtils_alert(
            "No conceptual mass family instances found.\n\n"
            "Select one or more placed mass family instances and run again,\n"
            "or run without a selection to target all mass instances in the project.\n\n"
            "Note: DirectShape masses are not targeted by this tool.",
            title=TITLE,
        )
        return

    preview_lines = [
        "Scope:          {}".format(scope_label),
        "Instances found: {}".format(len(elements)),
        "",
        "Each instance will be converted to a DirectShape in the Mass category,",
        "embedded directly in the project (no external family file dependency).",
        "Original family instances will be kept - delete them manually after verifying.",
        "",
        "Preview of first 20 instances:",
    ]
    for el in elements[:20]:
        preview_lines.append("  ID {:>8}  {}".format(_elem_id_int(el.Id), _instance_display_name(el)))
    if len(elements) > 20:
        preview_lines.append("  ... and {} more".format(len(elements) - 20))

    if not ui.uiUtils_show_text_report(
        "{} - Preview".format(TITLE),
        "\n".join(preview_lines),
        ok_text="Convert",
        cancel_text="Cancel",
        width=620,
        height=480,
    ):
        return

    created = []
    failures = []
    category_id = DB.ElementId(DB.BuiltInCategory.OST_Mass)

    tx = DB.Transaction(doc, TITLE)
    tx.Start()
    for el in elements:
        sub = DB.SubTransaction(doc)
        sub.Start()
        try:
            solids = _get_instance_solids(el)
            if not solids:
                raise Exception("No solid geometry found in this mass instance.")

            ds = DB.DirectShape.CreateElement(doc, category_id)
            ds.ApplicationId = "WWPTools.MassToInPlace"
            ds.ApplicationDataId = str(_elem_id_int(el.Id))

            geo_list = List[DB.GeometryObject]()
            for solid in solids:
                geo_list.Add(solid)
            ds.SetShape(geo_list)

            source_name = _instance_display_name(el)
            try:
                ds.Name = source_name[:80]
            except Exception:
                pass

            _set_comment(ds, "Converted from Mass Family | {}".format(source_name))
            created.append(ds)
            sub.Commit()
        except Exception as ex:
            try:
                sub.RollBack()
            except Exception:
                pass
            failures.append(
                "ID {} ({}): {}".format(_elem_id_int(el.Id), _instance_display_name(el), str(ex))
            )
    tx.Commit()

    try:
        sel_ids = List[DB.ElementId]()
        for ds in created:
            sel_ids.Add(ds.Id)
        uidoc.Selection.SetElementIds(sel_ids)
    except Exception:
        pass

    result_lines = [
        "Converted:  {}".format(len(created)),
        "Failed:     {}".format(len(failures)),
    ]
    if failures:
        result_lines += ["", "Failures:"] + ["  " + f for f in failures[:20]]
        if len(failures) > 20:
            result_lines.append("  ... and {} more".format(len(failures) - 20))

    ui.uiUtils_show_text_report(
        "{} - Results".format(TITLE),
        "\n".join(result_lines),
        ok_text="Close",
        cancel_text=None,
        width=580,
        height=380,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            ui.uiUtils_alert(
                "{}\n\n{}".format(exc, traceback.format_exc()),
                title="{} - Error".format(TITLE),
            )
        except Exception:
            UI.TaskDialog.Show(
                "{} - Error".format(TITLE),
                "{}\n\n{}".format(exc, traceback.format_exc()),
            )
