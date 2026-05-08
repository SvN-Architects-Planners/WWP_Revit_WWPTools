# -*- coding: utf-8 -*-
#! python3
"""
For each source view on a chosen level, create an equivalent view on the
target level(s), copying the exact ViewFamilyType, ViewTemplate, and all
writable instance parameters (browser-organisation fields, subcategory, etc.).
"""
import traceback

from Autodesk.Revit import DB
import WWP_uiUtils as ui


def _collect_levels(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    levels.sort(key=lambda l: l.Elevation)
    return levels


def _collect_area_schemes(doc):
    return list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme).ToElements())


def _views_for_level(doc, level):
    """Return all non-template ViewPlan elements whose GenLevel matches the given level."""
    result = []
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if v.IsTemplate:
            continue
        gen_level = v.GenLevel
        if gen_level is not None and gen_level.Id == level.Id:
            result.append(v)
    return result


def _copy_writable_params(src, dst):
    """Copy all writable instance parameters from src view to dst view."""
    # Built-in parameters that are set automatically or are meaningless to copy
    _SKIP_BIPS = {
        DB.BuiltInParameter.VIEW_NAME,
        DB.BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM,
        DB.BuiltInParameter.ELEM_FAMILY_PARAM,
        DB.BuiltInParameter.ELEM_TYPE_PARAM,
        DB.BuiltInParameter.PLAN_VIEW_LEVEL,
        DB.BuiltInParameter.VIEW_PHASE,
        DB.BuiltInParameter.VIEW_PHASE_FILTER,
    }

    for param in src.Parameters:
        if param.IsReadOnly:
            continue
        if param.StorageType == DB.StorageType.None:
            continue

        defn = param.Definition
        if defn is None:
            continue

        # Skip specific built-in params
        try:
            bip = defn.BuiltInParameter
            if bip in _SKIP_BIPS:
                continue
        except Exception:
            pass

        try:
            dst_param = dst.LookupParameter(defn.Name)
            if dst_param is None or dst_param.IsReadOnly:
                continue

            st = param.StorageType
            if st == DB.StorageType.String:
                val = param.AsString()
                dst_param.Set(val if val is not None else "")
            elif st == DB.StorageType.Integer:
                dst_param.Set(param.AsInteger())
            elif st == DB.StorageType.Double:
                dst_param.Set(param.AsDouble())
            elif st == DB.StorageType.ElementId:
                eid = param.AsElementId()
                if eid is not None and eid != DB.ElementId.InvalidElementId:
                    dst_param.Set(eid)
        except Exception:
            pass


def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        ui.uiUtils_alert("No active Revit document found.", title="Duplicate Views For Level")
        return

    doc = uidoc.Document
    levels = _collect_levels(doc)
    if not levels:
        ui.uiUtils_alert("No levels found in the document.", title="Duplicate Views For Level")
        return

    level_names = [lvl.Name for lvl in levels]

    # --- Pick source level ---
    src_idx = ui.uiUtils_select_indices(
        level_names,
        title="Source Level",
        prompt="Select the level whose views you want to duplicate:",
        multiselect=False,
        width=520,
        height=520,
    )
    if not src_idx:
        return
    source_level = levels[src_idx[0]]

    source_views = _views_for_level(doc, source_level)
    if not source_views:
        ui.uiUtils_alert(
            "No views found associated with '{}'.".format(source_level.Name),
            title="Duplicate Views For Level",
        )
        return

    # --- Pick target level(s) ---
    other_levels = [l for l in levels if l.Id != source_level.Id]
    other_names = [l.Name for l in other_levels]
    if not other_names:
        ui.uiUtils_alert("No other levels available as targets.", title="Duplicate Views For Level")
        return

    tgt_idx = ui.uiUtils_select_indices(
        other_names,
        title="Target Level(s)",
        prompt="Select target level(s) to create matching views on:",
        multiselect=True,
        width=520,
        height=520,
    )
    if not tgt_idx:
        return
    target_levels = [other_levels[i] for i in tgt_idx]

    # --- Create views ---
    created = []
    failed = []
    skipped = []

    t = DB.Transaction(doc, "Duplicate Views For Level")
    t.Start()
    try:
        for target_level in target_levels:
            for src_view in source_views:
                vtype = src_view.ViewType

                try:
                    if vtype in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan):
                        # Preserve the exact ViewFamilyType from the source view
                        new_view = DB.ViewPlan.Create(doc, src_view.GetTypeId(), target_level.Id)

                    elif vtype == DB.ViewType.AreaPlan:
                        src_scheme = src_view.AreaScheme
                        if src_scheme is None:
                            failed.append("No area scheme on source view '{}'".format(src_view.Name))
                            continue
                        new_view = DB.ViewPlan.CreateAreaPlan(doc, src_scheme.Id, target_level.Id)

                    else:
                        skipped.append("'{}' — unsupported type ({})".format(src_view.Name, vtype))
                        continue

                    # Apply view template first (before params, template may lock some)
                    tmpl_id = src_view.ViewTemplateId
                    if tmpl_id and tmpl_id != DB.ElementId.InvalidElementId:
                        try:
                            new_view.ViewTemplateId = tmpl_id
                        except Exception:
                            pass

                    # Copy all writable instance parameters
                    _copy_writable_params(src_view, new_view)

                    created.append("{} → {}".format(src_view.Name, new_view.Name))

                except Exception as ex:
                    failed.append("'{}' on '{}': {}".format(src_view.Name, target_level.Name, str(ex)))

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    summary = [
        "Source: {} ({} views)".format(source_level.Name, len(source_views)),
        "Target(s): {}".format(", ".join(l.Name for l in target_levels)),
        "",
        "Created: {}".format(len(created)),
        "Failed:  {}".format(len(failed)),
        "Skipped: {}".format(len(skipped)),
    ]
    if created:
        summary.append("\nCreated:")
        summary.extend("  " + n for n in created[:30])
        if len(created) > 30:
            summary.append("  ... {} more".format(len(created) - 30))
    if failed:
        summary.append("\nFailed:")
        summary.extend("  " + n for n in failed[:20])
    if skipped:
        summary.append("\nSkipped:")
        summary.extend("  " + n for n in skipped[:20])

    ui.uiUtils_alert("\n".join(summary), title="Duplicate Views For Level")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="Duplicate Views For Level - Error")
