# -*- coding: utf-8 -*-
import os
import sys
import traceback

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from pyrevit import DB
from System.Collections.Generic import List

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Select Doors in View"


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    active_view = doc.ActiveView
    if active_view is None:
        ui.uiUtils_alert("No active view.", title=WINDOW_TITLE)
        return

    door_ids = [
        e.Id
        for e in DB.FilteredElementCollector(doc, active_view.Id)
            .OfCategory(DB.BuiltInCategory.OST_Doors)
            .WhereElementIsNotElementType()
            .ToElements()
    ]

    if not door_ids:
        ui.uiUtils_alert("No doors found in the active view.", title=WINDOW_TITLE)
        return

    id_col = List[DB.ElementId]()
    for eid in door_ids:
        id_col.Add(eid)
    uidoc.Selection.SetElementIds(id_col)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
