# -*- coding: utf-8 -*-
"""Convert Lines to Detail Items.

Replaces FRR-styled detail lines in the current view with corresponding
'WWP_FireRating_FRR Line Based' detail component instances along the same curves.
Original detail lines are NOT deleted (they are supplemented by the components).

FRR line style -> family type mapping:
  'FRR - 0H'   -> Textline 0min FRR
  'FRR - 3/4H' -> Textline 45min FRR
  'FRR - 1H'   -> Textline 60min FRR
  'FRR - 1.5H' -> Textline 90min FRR
  'FRR - 2H'   -> Textline 120min FRR
  'FRR - 3H'   -> Textline 180min FRR
"""
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
WINDOW_TITLE = "Convert Lines to Detail Items"

# Family name for the WWP fire-rating line-based annotation component
FRR_FAMILY_NAME = "WWP_FireRating_FRR Line Based"

# Mapping: substring in line style name -> family type name
FRR_STYLE_TO_TYPE = [
    ("FRR - 0H",   "Textline 0min FRR"),
    ("FRR - 3/4H", "Textline 45min FRR"),
    ("FRR - 1H",   "Textline 60min FRR"),
    ("FRR - 1.5H", "Textline 90min FRR"),
    ("FRR - 2H",   "Textline 120min FRR"),
    ("FRR - 3H",   "Textline 180min FRR"),
]


def get_frr_family_types():
    """Return dict: type_name -> FamilySymbol for WWP_FireRating_FRR Line Based."""
    result = {}
    for sym in (DB.FilteredElementCollector(doc)
                .OfClass(DB.FamilySymbol)
                .ToElements()):
        fam = sym.Family
        if fam is None:
            continue
        if fam.Name == FRR_FAMILY_NAME:
            type_name = DB.Element.Name.GetValue(sym)
            result[type_name] = sym
    return result


def collect_frr_lines_in_view(view):
    """Return list of (line_element, style_name) for all FRR detail lines in view."""
    lines = []
    for elem in (DB.FilteredElementCollector(doc, view.Id)
                 .OfCategory(DB.BuiltInCategory.OST_Lines)
                 .WhereElementIsNotElementType()
                 .ToElements()):
        try:
            style = elem.LineStyle
            if style and "FRR" in (style.Name or ""):
                lines.append((elem, style.Name))
        except Exception:
            pass
    return lines


def get_style_key(style_name):
    """Match a line style name to the best FRR key."""
    for key, _ in FRR_STYLE_TO_TYPE:
        if key.upper() in style_name.upper():
            return key
    return None


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    active_view = doc.ActiveView
    frr_lines = collect_frr_lines_in_view(active_view)
    if not frr_lines:
        ui.uiUtils_alert(
            "No FRR detail lines found in the current view.",
            title=WINDOW_TITLE,
        )
        return

    family_types = get_frr_family_types()
    if not family_types:
        ui.uiUtils_alert(
            "Family '{}' not found in the project.\n"
            "Load the WWP_FireRating_FRR Line Based family first.".format(FRR_FAMILY_NAME),
            title=WINDOW_TITLE,
        )
        return

    # Build mapping: style_key -> FamilySymbol
    style_to_symbol = {}
    for style_key, type_name in FRR_STYLE_TO_TYPE:
        sym = family_types.get(type_name)
        if sym is not None:
            style_to_symbol[style_key] = sym

    msg = (
        "Convert {} FRR detail line(s) to '{}' components?\n"
        "The original detail lines will be kept."
    ).format(len(frr_lines), FRR_FAMILY_NAME)
    if not ui.uiUtils_confirm(msg, title=WINDOW_TITLE):
        return

    placed = 0
    skipped = 0

    t = DB.Transaction(doc, "Convert Lines to Detail Items")
    t.Start()
    try:
        # Activate all symbols first
        activated = set()
        for sym in style_to_symbol.values():
            if not sym.IsActive and id(sym) not in activated:
                sym.Activate()
                activated.add(id(sym))
        if activated:
            doc.Regenerate()

        for line_elem, style_name in frr_lines:
            style_key = get_style_key(style_name)
            sym = style_to_symbol.get(style_key) if style_key else None
            if sym is None:
                skipped += 1
                continue
            try:
                curve = line_elem.GeometryCurve
                doc.Create.NewFamilyInstance(
                    curve, sym, active_view
                )
                placed += 1
            except Exception:
                skipped += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    lines = ["Placed {} detail component(s).".format(placed)]
    if skipped:
        lines.append("{} line(s) skipped (no matching family type or error).".format(skipped))
    ui.uiUtils_alert("\n".join(lines), title=WINDOW_TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
