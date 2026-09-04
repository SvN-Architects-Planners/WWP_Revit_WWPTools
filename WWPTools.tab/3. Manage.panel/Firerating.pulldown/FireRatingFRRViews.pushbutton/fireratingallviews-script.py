# -*- coding: utf-8 -*-
"""Create Fire Rating Lines for All Walls in FRR Views.

Collects all views whose name contains 'FRR' (excluding Dependent views),
deletes any existing FRR detail lines in those views, then redraws them
based on each wall's FRR Walls parameter value.
"""
import os
import sys
import traceback
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")


from System.IO import File

from System.Windows.Controls import ListBoxItem
from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_versioning import apply_window_title
from pyrevit import DB
from System.Collections.Generic import List

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Create Fire Rating Lines - FRR Views"
FRR_PARAM_NAME = "FRR Walls"
FRR_VIEW_INDICATOR = "FRR"


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


def get_all_line_styles():
    lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    return {sub.Name: sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            for sub in lines_cat.SubCategories}


def find_style_for_frr(frr_value, all_styles):
    if not frr_value:
        return None
    frr_upper = frr_value.strip().upper()
    for name, style in all_styles.items():
        if "FRR" in name.upper() and frr_upper in name.upper():
            return style
    frr_dash = frr_upper.replace("/", "-").replace(".", "-")
    for name, style in all_styles.items():
        if "FRR" in name.upper() and frr_dash in name.upper():
            return style
    return None


def collect_frr_views():
    """Collect floor/section/detail views with 'FRR' in name, excluding Dependent views."""
    eligible_types = {
        DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan,
        DB.ViewType.Section, DB.ViewType.Elevation, DB.ViewType.Detail,
    }
    result = []
    for view in (DB.FilteredElementCollector(doc)
                 .OfClass(DB.View)
                 .ToElements()):
        if view.IsTemplate:
            continue
        if view.ViewType not in eligible_types:
            continue
        name = view.Name or ""
        if FRR_VIEW_INDICATOR.upper() not in name.upper():
            continue
        if "DEPENDENT" in name.upper():
            continue
        result.append(view)
    result.sort(key=lambda v: v.Name or "")
    return result


def collect_existing_frr_lines(view):
    lines = []
    for elem in (DB.FilteredElementCollector(doc, view.Id)
                 .OfCategory(DB.BuiltInCategory.OST_Lines)
                 .WhereElementIsNotElementType()
                 .ToElements()):
        try:
            style = elem.LineStyle
            if style and "FRR" in (style.Name or ""):
                lines.append(elem)
        except Exception:
            pass
    return lines


class FRRViewsDialog(object):
    def __init__(self, frr_views):
        xaml_path = os.path.join(script_dir, "FireRatingFRRViewsWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        ui.uiUtils_ensure_theme()
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._lst = self.window.FindName("LstViews")
        self._btn_run = self.window.FindName("BtnRun")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")
        self._txt_param = self.window.FindName("TxtFrrParam")
        self._btn_select_all = self.window.FindName("BtnSelectAll")
        self._btn_deselect_all = self.window.FindName("BtnDeselectAll")

        self._txt_param.Text = FRR_PARAM_NAME

        for view in frr_views:
            item = ListBoxItem()
            item.Content = view.Name
            item.Tag = view
            item.IsSelected = True
            self._lst.Items.Add(item)

        self._btn_run.Click += self._on_run
        self._btn_cancel.Click += self._on_cancel
        self._btn_select_all.Click += self._on_select_all
        self._btn_deselect_all.Click += self._on_deselect_all

        self.selected_views = []
        self.frr_param = None
        self.accepted = False

    def _on_select_all(self, sender, args):
        for item in self._lst.Items:
            item.IsSelected = True

    def _on_deselect_all(self, sender, args):
        for item in self._lst.Items:
            item.IsSelected = False

    def _on_run(self, sender, args):
        self.frr_param = (self._txt_param.Text or "").strip()
        if not self.frr_param:
            self._lbl_status.Text = "FRR parameter name is required."
            return
        self.selected_views = [
            item.Tag for item in self._lst.Items if item.IsSelected
        ]
        if not self.selected_views:
            self._lbl_status.Text = "Select at least one view."
            return
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


def process_view(view, frr_param_name, all_styles):
    walls = [
        e for e in DB.FilteredElementCollector(doc, view.Id)
            .OfCategory(DB.BuiltInCategory.OST_Walls)
            .WhereElementIsNotElementType()
            .ToElements()
    ]
    created = 0
    for wall in walls:
        loc = wall.Location
        if not isinstance(loc, DB.LocationCurve):
            continue
        param = wall.LookupParameter(frr_param_name)
        if param is None:
            continue
        frr_val = ""
        if param.StorageType == DB.StorageType.String:
            frr_val = param.AsString() or ""
        if not frr_val or frr_val.upper() == "0HR":
            continue
        try:
            detail_line = doc.Create.NewDetailCurve(view, loc.Curve)
            style = find_style_for_frr(frr_val, all_styles)
            if style is not None:
                detail_line.LookupParameter("Line Style").Set(style.Id)
            created += 1
        except Exception:
            pass
    return created


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    frr_views = collect_frr_views()
    if not frr_views:
        ui.uiUtils_alert(
            "No views found with '{}' in their name (excluding Dependent views).\n"
            "Rename views to include 'FRR' to use this tool.".format(FRR_VIEW_INDICATOR),
            title=WINDOW_TITLE,
        )
        return

    dialog = FRRViewsDialog(frr_views)
    if not dialog.ShowDialog():
        return

    all_styles = get_all_line_styles()
    total_deleted = 0
    total_created = 0
    errors = []

    t = DB.Transaction(doc, "Create Fire Rating Lines (All FRR Views)")
    t.Start()
    try:
        # Step 1: Delete existing FRR lines in selected views
        for view in dialog.selected_views:
            existing = collect_existing_frr_lines(view)
            if existing:
                id_col = List[DB.ElementId]()
                for ln in existing:
                    id_col.Add(ln.Id)
                doc.Delete(id_col)
                total_deleted += len(existing)

        # Step 2: Regenerate so new lines can be placed
        doc.Regenerate()

        # Step 3: Create new FRR lines
        for view in dialog.selected_views:
            try:
                created = process_view(view, dialog.frr_param, all_styles)
                total_created += created
            except Exception as ex:
                errors.append("{}: {}".format(view.Name, str(ex)))

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    msg = "Deleted {} old line(s). Created {} new fire rating line(s) across {} view(s).".format(
        total_deleted, total_created, len(dialog.selected_views))
    if errors:
        msg += "\n\nErrors:\n" + "\n".join(errors)
    ui.uiUtils_alert(msg, title=WINDOW_TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
