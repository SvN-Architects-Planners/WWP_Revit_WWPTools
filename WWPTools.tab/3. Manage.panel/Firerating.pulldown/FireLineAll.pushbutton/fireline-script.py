# -*- coding: utf-8 -*-
import os
import sys
import traceback
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")


from System.IO import File

from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Controls import ComboBoxItem

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_versioning import apply_window_title
from pyrevit import DB

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Create Non-Rated Line for ALL Walls"


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


def get_all_line_styles():
    lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    styles = []
    for sub in lines_cat.SubCategories:
        styles.append(sub.Name)
    styles.sort()
    return styles


def get_line_style_by_name(name):
    lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    for sub in lines_cat.SubCategories:
        if sub.Name == name:
            return sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
    return None


class FireLineDialog(object):
    def __init__(self, line_styles):
        ui.uiUtils_ensure_theme()
        xaml_path = os.path.join(script_dir, "FireLineWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._combo = self.window.FindName("CboLineStyle")
        self._btn_run = self.window.FindName("BtnRun")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")

        default_style = "WWP - FRR - PH"
        default_idx = 0
        for i, name in enumerate(line_styles):
            item = ComboBoxItem()
            item.Content = name
            self._combo.Items.Add(item)
            if name == default_style:
                default_idx = i
        if self._combo.Items.Count > 0:
            self._combo.SelectedIndex = default_idx

        self._btn_run.Click += self._on_run
        self._btn_cancel.Click += self._on_cancel

        self.selected_style = None
        self.accepted = False

    def _on_run(self, sender, args):
        sel = self._combo.SelectedItem
        if sel is None:
            self._lbl_status.Text = "Please select a line style."
            return
        self.selected_style = sel.Content
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


def create_fire_lines(style_name):
    active_view = doc.ActiveView
    graphic_style = get_line_style_by_name(style_name)

    walls = [
        e for e in DB.FilteredElementCollector(doc, active_view.Id)
            .OfCategory(DB.BuiltInCategory.OST_Walls)
            .WhereElementIsNotElementType()
            .ToElements()
    ]

    created = 0
    t = DB.Transaction(doc, "Create Fire Rating Lines")
    t.Start()
    try:
        for wall in walls:
            loc = wall.Location
            if not isinstance(loc, DB.LocationCurve):
                continue
            curve = loc.Curve
            try:
                detail_line = doc.Create.NewDetailCurve(active_view, curve)
                if graphic_style is not None:
                    detail_line.LineStyle = graphic_style
                created += 1
            except Exception:
                pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    return created


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    line_styles = get_all_line_styles()
    if not line_styles:
        ui.uiUtils_alert("No line styles found in the project.", title=WINDOW_TITLE)
        return

    dialog = FireLineDialog(line_styles)
    if not dialog.ShowDialog():
        return

    created = create_fire_lines(dialog.selected_style)
    ui.uiUtils_alert(
        "Created {} detail line(s) with style '{}'.".format(created, dialog.selected_style),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
