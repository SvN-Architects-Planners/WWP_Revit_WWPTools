#!python3
# -*- coding: utf-8 -*-
"""Create Fire Rating Lines for ALL Walls in Current View.

Default FRR parameter name: 'FRR'
Line style naming convention: 'WWP - FRR - xH'
Ratings handled: 0HR, .75HR (3/4HR), 1HR, 1.5HR, 2HR, 3HR

Settings saved to: %APPDATA%\\pyRevit\\WWPTools\\FireRatingAll.settings.json
"""
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
from System.Windows.Controls import ComboBoxItem, Label, ComboBox, Grid, ColumnDefinition
from System.Windows import GridLength, Thickness

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_versioning import apply_window_title
from WWP_settings import get_tool_settings
from pyrevit import DB

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Create Fire Rating Lines \u2014 All Walls"
TOOL_NAME = "FireRatingAll"

# Standard rating strings (substrings to match in parameter value)
FRR_RATINGS = ["1.5HR", ".75HR", "0HR", "1HR", "2HR", "3HR"]


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


def get_all_line_styles():
    lines_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines)
    return {sub.Name: sub.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
            for sub in lines_cat.SubCategories}


def _norm_token(frr_value):
    """Convert a FRR value string to a normalised token for style matching."""
    token = frr_value.strip().upper().replace("HR", "").replace(" ", "")
    if token in (".75", "75", "0.75", "3/4"):
        return "3/4"
    return token


def find_style_for_frr(frr_value, all_styles):
    if not frr_value:
        return None
    candidates = {n: s for n, s in all_styles.items() if "FRR" in n.upper()}
    token = _norm_token(frr_value)
    for name, style in candidates.items():
        if token in name.upper().replace(" ", ""):
            return style
    return None


def find_style_by_name(name, all_styles):
    return all_styles.get(name)


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class FireRatingAllDialog(object):
    def __init__(self, default_param):
        xaml_path = os.path.join(script_dir, "FireRatingWindow.xaml")
        self.window = XamlReader.Parse(File.ReadAllText(xaml_path))
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._txt_param = self.window.FindName("TxtFrrParam")
        self._btn_run = self.window.FindName("BtnRun")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")
        self._lbl_count = self.window.FindName("LblWallCount")

        self._txt_param.Text = default_param
        self._update_count()

        self._btn_run.Click += self._on_run
        self._btn_cancel.Click += self._on_cancel
        self._txt_param.TextChanged += lambda s, a: setattr(self, '_status_clear', True)

        self.frr_param = None
        self.accepted = False

    def _update_count(self):
        try:
            count = (DB.FilteredElementCollector(doc, doc.ActiveView.Id)
                     .OfCategory(DB.BuiltInCategory.OST_Walls)
                     .WhereElementIsNotElementType()
                     .GetElementCount())
            self._lbl_count.Text = "{} wall(s) visible in current view.".format(count)
        except Exception:
            self._lbl_count.Text = ""

    def _on_run(self, sender, args):
        self.frr_param = (self._txt_param.Text or "").strip()
        if not self.frr_param:
            self._lbl_status.Text = "FRR parameter name is required."
            return
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


# ---------------------------------------------------------------------------
# Fallback mapping dialog
# ---------------------------------------------------------------------------

class FRRMappingDialog(object):
    """Shows when some FRR values have no automatically matched line style."""

    def __init__(self, unmapped_values, all_style_names, saved_map):
        xaml_path = os.path.join(script_dir, "FRRMappingWindow.xaml")
        self.window = XamlReader.Parse(File.ReadAllText(xaml_path))
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._panel = self.window.FindName("MappingPanel")
        self._btn_ok = self.window.FindName("BtnOK")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")

        self._combos = {}  # frr_val -> ComboBox
        self._all_style_names = all_style_names

        for frr_val in unmapped_values:
            self._add_row(frr_val, saved_map.get(frr_val, ""))

        self._btn_ok.Click += self._on_ok
        self._btn_cancel.Click += self._on_cancel

        self.mapping = {}  # frr_val -> style_name ("" means skip)
        self.accepted = False

    def _add_row(self, frr_val, saved_style):
        row_grid = Grid()
        row_grid.Margin = Thickness(0, 0, 0, 6)
        col1 = ColumnDefinition()
        col1.Width = GridLength(120)
        col2 = ColumnDefinition()
        col2.Width = GridLength(1, System_Windows_GridUnitType_Star())
        row_grid.ColumnDefinitions.Add(col1)
        row_grid.ColumnDefinitions.Add(col2)

        lbl = Label()
        lbl.Content = frr_val
        lbl.Foreground = _hex_brush("#17324D")
        Grid.SetColumn(lbl, 0)

        combo = ComboBox()
        combo.Height = 30
        combo.FontSize = 12
        combo.Margin = Thickness(6, 0, 0, 0)
        Grid.SetColumn(combo, 1)

        skip_item = ComboBoxItem()
        skip_item.Content = "\u2014 Skip —"
        skip_item.Tag = ""
        combo.Items.Add(skip_item)
        combo.SelectedIndex = 0

        for name in self._all_style_names:
            item = ComboBoxItem()
            item.Content = name
            item.Tag = name
            combo.Items.Add(item)
            if name == saved_style:
                combo.SelectedItem = item

        row_grid.Children.Add(lbl)
        row_grid.Children.Add(combo)
        self._panel.Children.Add(row_grid)
        self._combos[frr_val] = combo

    def _on_ok(self, sender, args):
        self.mapping = {}
        for frr_val, combo in self._combos.items():
            sel = combo.SelectedItem
            self.mapping[frr_val] = sel.Tag if sel is not None else ""
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


def _hex_brush(hex_color):
    from System.Windows.Media import SolidColorBrush, Color
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return SolidColorBrush(Color.FromRgb(r, g, b))


def System_Windows_GridUnitType_Star():
    from System.Windows import GridUnitType
    return GridUnitType.Star


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def build_style_map(walls, frr_param_name, all_styles, saved_custom_map):
    """Collect unique FRR values, auto-map styles, return map and any unmapped."""
    unique_vals = set()
    for wall in walls:
        p = wall.LookupParameter(frr_param_name)
        if p and p.StorageType == DB.StorageType.String:
            v = (p.AsString() or "").strip()
            if v:
                unique_vals.add(v)

    style_map = {}      # frr_val -> GraphicsStyle or None
    unmapped = []       # frr_vals with no auto or saved match
    for val in sorted(unique_vals):
        style = find_style_for_frr(val, all_styles)
        if style is None and val in saved_custom_map:
            style = find_style_by_name(saved_custom_map[val], all_styles)
        style_map[val] = style
        if style is None and val.upper() != "0HR":
            unmapped.append(val)
    return style_map, unmapped


def create_frr_lines(walls, frr_param_name, style_map, active_view):
    created = 0
    skipped_no_param = 0
    skipped_no_style = 0

    t = DB.Transaction(doc, "Create Fire Rating Lines")
    t.Start()
    try:
        for wall in walls:
            loc = wall.Location
            if not isinstance(loc, DB.LocationCurve):
                continue
            p = wall.LookupParameter(frr_param_name)
            if p is None:
                skipped_no_param += 1
                continue
            frr_val = (p.AsString() or "").strip() if p.StorageType == DB.StorageType.String else ""
            if not frr_val or frr_val.upper() == "0HR":
                continue

            style = style_map.get(frr_val)
            if style is None:
                skipped_no_style += 1
                continue

            try:
                dl = doc.Create.NewDetailCurve(active_view, loc.Curve)
                ls_p = dl.LookupParameter("Line Style")
                if ls_p:
                    ls_p.Set(style.Id)
                created += 1
            except Exception:
                pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    return created, skipped_no_param, skipped_no_style


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    settings, save_settings = get_tool_settings(TOOL_NAME, doc=doc)
    default_param = getattr(settings, "frr_param", None) or "FRR"
    saved_custom_map = getattr(settings, "frr_custom_map", None) or {}

    active_view = doc.ActiveView

    dialog = FireRatingAllDialog(default_param)
    if not dialog.ShowDialog():
        return

    walls = [
        e for e in DB.FilteredElementCollector(doc, active_view.Id)
            .OfCategory(DB.BuiltInCategory.OST_Walls)
            .WhereElementIsNotElementType()
            .ToElements()
    ]
    if not walls:
        ui.uiUtils_alert("No walls found in the current view.", title=WINDOW_TITLE)
        return

    all_styles = get_all_line_styles()
    style_map, unmapped = build_style_map(walls, dialog.frr_param, all_styles, saved_custom_map)

    # If any FRR values have no matching style, ask user to map them
    custom_mapping = dict(saved_custom_map)
    if unmapped:
        all_style_names = sorted(all_styles.keys())
        map_dialog = FRRMappingDialog(unmapped, all_style_names, saved_custom_map)
        if not map_dialog.ShowDialog():
            return
        for frr_val, style_name in map_dialog.mapping.items():
            custom_mapping[frr_val] = style_name
            if style_name:
                style_map[frr_val] = find_style_by_name(style_name, all_styles)

    created, skip_param, skip_style = create_frr_lines(
        walls, dialog.frr_param, style_map, active_view
    )

    # Persist settings
    settings.frr_param = dialog.frr_param
    settings.frr_custom_map = custom_mapping
    save_settings()

    msgs = ["Created {} fire rating line(s).".format(created)]
    if skip_style:
        msgs.append("{} wall(s) skipped \u2014 no matching line style.".format(skip_style))
    if skip_param:
        msgs.append("{} wall(s) skipped \u2014 missing '{}' parameter.".format(
            skip_param, dialog.frr_param))
    ui.uiUtils_alert("\n".join(msgs), title=WINDOW_TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
