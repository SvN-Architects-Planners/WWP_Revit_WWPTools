#! python3
# -*- coding: utf-8 -*-
import traceback

from Autodesk.Revit import DB
import WWP_uiUtils as ui


VIEW_TYPES = ["RCP", "Floor Plan", "Area Plan"]


def _collect_levels(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    levels.sort(key=lambda l: l.Elevation)
    return levels


def _collect_area_schemes(doc):
    schemes = list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme).ToElements())
    schemes.sort(key=lambda s: (getattr(s, "Name", "") or "").lower())
    return schemes


def _collect_view_templates(doc):
    templates = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View) if v.IsTemplate]
    templates.sort(key=lambda v: (getattr(v, "Name", "") or "").lower())
    return templates


def _view_family_type(doc, family):
    types = [t for t in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType) if t.ViewFamily == family]
    return types[0] if types else None


def _set_param(view, name, value):
    try:
        param = view.LookupParameter(name)
        if param and not param.IsReadOnly:
            if isinstance(value, DB.ElementId):
                param.Set(value)
            elif isinstance(value, str):
                param.Set(value)
            else:
                param.Set(str(value))
            return True
    except Exception:
        pass
    return False


def _choose_template(templates, title, prompt):
    if not templates:
        return None
    names = [t.Name for t in templates]
    idx = ui.uiUtils_select_indices(
        names,
        title=title,
        prompt=prompt,
        multiselect=False,
        width=520,
        height=420,
    )
    if not idx:
        return None
    return templates[idx[0]]


def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        ui.uiUtils_alert("No active Revit document found.", title="Create Views From Level")
        return

    doc = uidoc.Document

    levels = _collect_levels(doc)
    if not levels:
        ui.uiUtils_alert("No levels found in the document.", title="Create Views From Level")
        return

    level_names = [lvl.Name for lvl in levels]
    level_indices = ui.uiUtils_select_indices(
        level_names,
        title="Choose Levels",
        prompt="Select levels:",
        multiselect=True,
        width=520,
        height=520,
    )
    if not level_indices:
        return
    selected_levels = [levels[i] for i in level_indices]

    view_type_indices = ui.uiUtils_select_indices(
        VIEW_TYPES,
        title="View Types",
        prompt="Select view types to create:",
        multiselect=True,
        width=420,
        height=320,
    )
    if not view_type_indices:
        ui.uiUtils_alert("No view types selected.", title="Create Views From Level")
        return

    create_rcp = 0 in view_type_indices
    create_floor = 1 in view_type_indices
    create_area = 2 in view_type_indices

    templates = _collect_view_templates(doc)
    floor_template = _choose_template(templates, "Floor Plan Template", "Select Floor Plan template (Cancel to skip):") if create_floor else None
    rcp_template = _choose_template(templates, "RCP Template", "Select RCP template (Cancel to skip):") if create_rcp else None
    area_template = _choose_template(templates, "Area Plan Template", "Select Area Plan template (Cancel to skip):") if create_area else None

    scheme = None
    if create_area:
        schemes = _collect_area_schemes(doc)
        if schemes:
            scheme_names = [sch.Name for sch in schemes]
            scheme_indices = ui.uiUtils_select_indices(
                scheme_names,
                title="Choose an Area Scheme",
                prompt="Select an area scheme:",
                multiselect=False,
                width=520,
                height=420,
            )
            if scheme_indices:
                scheme = schemes[scheme_indices[0]]
        if scheme is None:
            ui.uiUtils_alert("No area scheme selected.", title="Create Views From Level")
            return

    floor_type = _view_family_type(doc, DB.ViewFamily.FloorPlan) if create_floor else None
    rcp_type = _view_family_type(doc, DB.ViewFamily.CeilingPlan) if create_rcp else None

    created = []
    failed = []

    t = DB.Transaction(doc, "Create Views From Level")
    t.Start()
    try:
        for level in selected_levels:
            if create_floor:
                if floor_type is None:
                    failed.append("{}: Floor plan type missing".format(level.Name))
                else:
                    try:
                        view = DB.ViewPlan.Create(doc, floor_type.Id, level.Id)
                        if floor_template:
                            view.ViewTemplateId = floor_template.Id
                        created.append(view.Name)
                    except Exception as ex:
                        failed.append("{}: Floor plan: {}".format(level.Name, str(ex)))

            if create_rcp:
                if rcp_type is None:
                    failed.append("{}: RCP type missing".format(level.Name))
                else:
                    try:
                        view = DB.ViewPlan.Create(doc, rcp_type.Id, level.Id)
                        if rcp_template:
                            view.ViewTemplateId = rcp_template.Id
                        created.append(view.Name)
                    except Exception as ex:
                        failed.append("{}: RCP: {}".format(level.Name, str(ex)))

            if create_area and scheme is not None:
                try:
                    area_view = DB.ViewPlan.CreateAreaPlan(doc, scheme.Id, level.Id)
                    if area_template:
                        area_view.ViewTemplateId = area_template.Id
                    view_type = doc.GetElement(area_view.GetTypeId())
                    view_type_name = getattr(view_type, "Name", "") if view_type else ""
                    _set_param(area_view, "View Category", view_type_name)
                    _set_param(area_view, "View Subcategory", "05 Area")
                    created.append(area_view.Name)
                except Exception as ex:
                    failed.append("{}: Area plan: {}".format(level.Name, str(ex)))

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    summary = [
        "Created: {}".format(len(created)),
        "Failed: {}".format(len(failed)),
    ]
    if created:
        summary.append("\nCreated views:")
        summary.extend(["- {}".format(name) for name in created[:20]])
        if len(created) > 20:
            summary.append("... {} more".format(len(created) - 20))
    if failed:
        summary.append("\nFailures:")
        summary.extend(["- {}".format(msg) for msg in failed[:20]])
        if len(failed) > 20:
            summary.append("... {} more".format(len(failed) - 20))

    ui.uiUtils_alert("\n".join(summary), title="Create Views From Level")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="Create Views From Level - Error")
