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
WINDOW_TITLE = "Clear Fire Rating Lines"


def collect_frr_lines(view):
    frr_lines = []
    for elem in (DB.FilteredElementCollector(doc, view.Id)
                 .OfCategory(DB.BuiltInCategory.OST_Lines)
                 .WhereElementIsNotElementType()
                 .ToElements()):
        try:
            style = elem.LineStyle
            if style and "FRR" in (style.Name or ""):
                frr_lines.append(elem)
        except Exception:
            pass
    return frr_lines


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    active_view = doc.ActiveView
    if active_view is None:
        ui.uiUtils_alert("No active view.", title=WINDOW_TITLE)
        return

    frr_lines = collect_frr_lines(active_view)
    if not frr_lines:
        ui.uiUtils_alert("No FRR detail lines found in the active view.", title=WINDOW_TITLE)
        return

    msg = "Delete {} FRR detail line(s) from the current view?".format(len(frr_lines))
    if not ui.uiUtils_confirm(msg, title=WINDOW_TITLE):
        return

    t = DB.Transaction(doc, "Clear Fire Rating Lines")
    t.Start()
    try:
        id_col = List[DB.ElementId]()
        for ln in frr_lines:
            id_col.Add(ln.Id)
        doc.Delete(id_col)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    ui.uiUtils_alert(
        "Deleted {} FRR line(s) from the view.".format(len(frr_lines)),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
