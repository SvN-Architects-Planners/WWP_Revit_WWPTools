"""Parking Demand Calculator - calculates required parking from room GFA by function type."""

import math
import os
import sys
import traceback

import clr
from pyrevit import DB, revit

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from WWP_versioning import apply_window_title  # noqa: E402


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

_SQFT_TO_SQM = 0.092903


def _sqm(sqft):
    return sqft * _SQFT_TO_SQM


# ---------------------------------------------------------------------------
# Default demand ratios: m2 of GFA per parking space, keyed by keyword
# (Based on common code/planning standards; user can override per function)
# ---------------------------------------------------------------------------

_DEFAULT_RATIOS = [
    ("Residential", 80.0),
    ("Apartment", 80.0),
    ("Suite", 80.0),
    ("Dwelling", 80.0),
    ("Hotel", 60.0),
    ("Motel", 60.0),
    ("Office", 35.0),
    ("Admin", 35.0),
    ("Retail", 25.0),
    ("Shop", 25.0),
    ("Store", 25.0),
    ("Restaurant", 15.0),
    ("Cafe", 15.0),
    ("Food", 15.0),
    ("Industrial", 70.0),
    ("Warehouse", 90.0),
    ("Storage", 100.0),
    ("Medical", 30.0),
    ("Clinic", 30.0),
    ("Hospital", 25.0),
    ("Education", 40.0),
    ("School", 40.0),
    ("Gym", 20.0),
    ("Fitness", 20.0),
    ("Entertainment", 12.0),
    ("Theatre", 10.0),
    ("Lobby", 200.0),
    ("Corridor", 500.0),
    ("Circulation", 500.0),
    ("Mechanical", 1000.0),
    ("Toilet", 1000.0),
    ("Stair", 1000.0),
]


def _get_default_ratio(func_name):
    lower = func_name.lower()
    for keyword, ratio in _DEFAULT_RATIOS:
        if keyword.lower() in lower:
            return ratio
    return 50.0


# ---------------------------------------------------------------------------
# Revit data collection
# ---------------------------------------------------------------------------

def _get_all_rooms(doc):
    rooms = []
    collector = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Rooms)
    for room in collector.WhereElementIsNotElementType():
        if room is None:
            continue
        try:
            if room.Area <= 0:
                continue
        except Exception:
            continue
        rooms.append(room)
    return rooms


def _get_room_function(room):
    for param_name in ["Department", "Room Type", "Occupancy"]:
        try:
            param = room.LookupParameter(param_name)
            if param is not None and param.HasValue:
                val = param.AsString()
                if val and val.strip():
                    return val.strip()
        except Exception:
            continue
    try:
        name = (room.Name or "").strip()
        if name:
            return name
    except Exception:
        pass
    return "Unassigned"


def _get_room_area_sqm(room):
    try:
        return _sqm(room.Area)
    except Exception:
        return 0.0


def _get_actual_parking_count(doc):
    try:
        collector = (
            DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_Parking)
            .WhereElementIsNotElementType()
        )
        return sum(1 for e in collector if e is not None)
    except Exception:
        return 0


def _build_function_data(rooms):
    """Returns {function_name: total_gfa_sqm} grouped by Department/name."""
    data = {}
    for room in rooms:
        func = _get_room_function(room)
        area = _get_room_area_sqm(room)
        data[func] = data.get(func, 0.0) + area
    return data


# ---------------------------------------------------------------------------
# DataTable builder
# ---------------------------------------------------------------------------

def _build_data_table(function_data):
    clr.AddReference("System.Data")
    from System.Data import DataTable, DataColumn
    from System import String

    table = DataTable("Functions")
    for col_name in ("Function", "GFA", "Ratio", "Demand"):
        table.Columns.Add(DataColumn(col_name, String))

    for func_name in sorted(function_data.keys(), key=lambda n: n.lower()):
        gfa = function_data[func_name]
        ratio = _get_default_ratio(func_name)
        demand = math.ceil(gfa / ratio) if ratio > 0 else 0
        row = table.NewRow()
        row["Function"] = func_name
        row["GFA"] = "{:.0f}".format(round(gfa))
        row["Ratio"] = "{:.0f}".format(ratio)
        row["Demand"] = str(demand)
        table.Rows.Add(row)

    return table


# ---------------------------------------------------------------------------
# WWP_uiUtils loader
# ---------------------------------------------------------------------------

def _load_uiutils():
    try:
        import WWP_uiUtils as ui
        return ui
    except Exception:
        from pyrevit import forms
        forms.alert(
            "WWP_uiUtils is not available. Restart pyRevit or reinstall WWPTools.",
            title="Parking Demand Calculator",
        )
        raise


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

def _show_dialog(function_data, actual_spots):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    clr.AddReference("System.Data")

    from System.IO import File, StringReader
    from System.Windows.Markup import XamlReader
    from System.Xml import XmlReader
    from System.Windows.Media import SolidColorBrush, Color
    from System import Uri
    from System.Windows.Media.Imaging import BitmapImage

    table = _build_data_table(function_data)

    xaml_path = os.path.join(script_dir, "ParkingDemandDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))
    xaml_text = File.ReadAllText(xaml_path)
    reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(reader)
    apply_window_title(window, "Parking Demand Calculator")

    # -- Named controls --
    grid = window.FindName("FunctionGrid")
    total_gfa_text = window.FindName("TotalGFAText")
    total_demand_text = window.FindName("TotalDemandText")
    summary_demand = window.FindName("SummaryDemandText")
    summary_ada = window.FindName("SummaryAdaText")
    summary_actual = window.FindName("SummaryActualText")
    summary_diff = window.FindName("SummaryDiffText")
    bar_fill = window.FindName("DemandBarFill")
    bar_label = window.FindName("DemandBarLabel")
    net_stall_area = window.FindName("NetStallAreaText")
    gross_stall_area = window.FindName("GrossStallAreaText")

    stall_w_slider = window.FindName("StallWidthSlider")
    stall_w_text = window.FindName("StallWidthText")
    stall_d_slider = window.FindName("StallDepthSlider")
    stall_d_text = window.FindName("StallDepthText")
    aisle_slider = window.FindName("AisleWidthSlider")
    aisle_text = window.FindName("AisleWidthText")
    ada_slider = window.FindName("AdaPctSlider")
    ada_text = window.FindName("AdaPctText")

    recalc_btn = window.FindName("RecalcButton")
    export_btn = window.FindName("ExportButton")
    ok_btn = window.FindName("OkButton")
    logo_image = window.FindName("LogoImage")

    # Bind DataTable to DataGrid
    grid.ItemsSource = table.DefaultView

    summary_actual.Text = str(actual_spots)

    # Load logo
    try:
        logo_path = os.path.join(lib_path, "WWPtools-logo.png")
        if os.path.isfile(logo_path):
            bmp = BitmapImage()
            bmp.BeginInit()
            bmp.UriSource = Uri(logo_path)
            bmp.CacheOption = BitmapImage.CacheOption.OnLoad
            bmp.EndInit()
            logo_image.Source = bmp
    except Exception:
        pass

    # -- Slider <-> TextBox sync --

    def _slider_to_text(slider, textbox, fmt):
        def _changed(s, e):
            textbox.Text = fmt.format(slider.Value)
        slider.ValueChanged += _changed

    def _text_to_slider(textbox, slider):
        def _changed(s, e):
            try:
                val = float(textbox.Text.replace(",", "."))
                if slider.Minimum <= val <= slider.Maximum:
                    slider.Value = val
            except Exception:
                pass
        textbox.TextChanged += _changed

    _slider_to_text(stall_w_slider, stall_w_text, "{:.1f}")
    _slider_to_text(stall_d_slider, stall_d_text, "{:.1f}")
    _slider_to_text(aisle_slider, aisle_text, "{:.1f}")
    _slider_to_text(ada_slider, ada_text, "{:.0f}")
    _text_to_slider(stall_w_text, stall_w_slider)
    _text_to_slider(stall_d_text, stall_d_slider)
    _text_to_slider(aisle_text, aisle_slider)
    _text_to_slider(ada_text, ada_slider)

    # -- Recalculate logic --

    def _recalculate():
        # Commit any pending cell edit before reading values
        try:
            from System.Windows.Controls import DataGridEditingUnit
            grid.CommitEdit(DataGridEditingUnit.Row, True)
        except Exception:
            try:
                grid.CommitEdit()
            except Exception:
                pass

        stall_w = stall_w_slider.Value
        stall_d = stall_d_slider.Value
        aisle_w = aisle_slider.Value
        ada_pct = ada_slider.Value

        total_gfa = 0.0
        total_demand = 0

        for row in table.Rows:
            try:
                gfa = float(str(row["GFA"]).replace(",", ""))
                ratio_str = str(row["Ratio"]).strip()
                ratio = float(ratio_str) if ratio_str else 50.0
                if ratio <= 0:
                    ratio = 50.0
                demand = math.ceil(gfa / ratio)
                row["Demand"] = str(demand)
                total_gfa += gfa
                total_demand += demand
            except Exception:
                continue

        ada_required = int(math.ceil(total_demand * ada_pct / 100.0))
        diff = actual_spots - total_demand

        total_gfa_text.Text = "{:,.0f}".format(total_gfa)
        total_demand_text.Text = str(total_demand)
        summary_demand.Text = str(total_demand)
        summary_ada.Text = str(ada_required)
        summary_actual.Text = str(actual_spots)

        diff_str = "+{}".format(diff) if diff >= 0 else str(diff)
        summary_diff.Text = diff_str

        if diff >= 0:
            summary_diff.Foreground = SolidColorBrush(Color.FromRgb(34, 197, 94))
        else:
            summary_diff.Foreground = SolidColorBrush(Color.FromRgb(220, 50, 50))

        # Update supply bar - need pixel width relative to container
        try:
            pct = min(100.0, actual_spots / float(total_demand) * 100.0) if total_demand > 0 else 100.0
        except ZeroDivisionError:
            pct = 100.0
        bar_label.Text = "{:.0f}% supplied".format(pct)

        # Resize the bar fill relative to container width
        try:
            container_width = bar_fill.Parent.ActualWidth
            bar_fill.Width = max(0.0, container_width * pct / 100.0)
        except Exception:
            pass

        # Stall area calculations
        # Net footprint = width x depth
        net_area = stall_w * stall_d
        # Gross area per space = width x (depth + half aisle)
        gross_area = stall_w * (stall_d + aisle_w / 2.0)
        net_stall_area.Text = "{:.1f} m2".format(net_area)
        gross_stall_area.Text = "{:.1f} m2".format(gross_area)

    def _on_recalc(sender, e):
        _recalculate()

    def _on_export(sender, e):
        _do_export()

    def _on_ok(sender, e):
        window.DialogResult = True
        window.Close()

    recalc_btn.Click += _on_recalc
    export_btn.Click += _on_export
    ok_btn.Click += _on_ok

    # -- Export report --

    def _do_export():
        ui = _load_uiutils()
        stall_w = stall_w_slider.Value
        stall_d = stall_d_slider.Value
        aisle_w = aisle_slider.Value
        ada_pct = ada_slider.Value
        gross_area = stall_w * (stall_d + aisle_w / 2.0)

        col_widths = (32, 10, 11, 8)
        header = (
            "{:<{w0}} {:>{w1}} {:>{w2}} {:>{w3}}".format(
                "Building Function", "GFA (m2)", "m2/Space", "Demand",
                w0=col_widths[0], w1=col_widths[1], w2=col_widths[2], w3=col_widths[3],
            )
        )
        separator = "-" * (sum(col_widths) + 3)
        rows_text = []
        total_gfa = 0.0
        total_demand = 0
        for row in table.Rows:
            func = str(row["Function"])
            gfa_str = str(row["GFA"])
            ratio_str = str(row["Ratio"])
            demand_str = str(row["Demand"])
            rows_text.append(
                "{:<{w0}} {:>{w1}} {:>{w2}} {:>{w3}}".format(
                    func, gfa_str, ratio_str, demand_str,
                    w0=col_widths[0], w1=col_widths[1], w2=col_widths[2], w3=col_widths[3],
                )
            )
            try:
                total_gfa += float(gfa_str.replace(",", ""))
                total_demand += int(demand_str)
            except Exception:
                pass

        total_row = (
            "{:<{w0}} {:>{w1}} {:>{w2}} {:>{w3}}".format(
                "TOTAL",
                "{:,.0f}".format(total_gfa),
                "",
                str(total_demand),
                w0=col_widths[0], w1=col_widths[1], w2=col_widths[2], w3=col_widths[3],
            )
        )

        diff = actual_spots - total_demand
        diff_str = "+{}".format(diff) if diff >= 0 else str(diff)
        ada_required = int(math.ceil(total_demand * ada_pct / 100.0))

        lines = [
            "Parking Demand Calculator",
            "=" * 65,
            "",
            header,
            separator,
        ] + rows_text + [
            separator,
            total_row,
            "",
            "=" * 65,
            "Summary",
            "=" * 65,
            "  Parking demand:        {:>6}".format(total_demand),
            "  ADA required ({:.0f}%):    {:>6}".format(ada_pct, ada_required),
            "  Actual spaces (model): {:>6}".format(actual_spots),
            "  Surplus / Deficit:     {:>6}".format(diff_str),
            "",
            "Parking Layout Standards",
            "-" * 40,
            "  Stall width:           {:.2f} m".format(stall_w),
            "  Stall depth:           {:.2f} m".format(stall_d),
            "  Drive aisle width:     {:.2f} m".format(aisle_w),
            "  Gross area per space:  {:.1f} m2 (incl. half aisle)".format(gross_area),
        ]

        ui.uiUtils_show_text_report(
            "Parking Demand Calculator - Report",
            "\n".join(lines),
            ok_text="Close",
            cancel_text=None,
            width=700,
            height=520,
        )

    # Initial calculation on open
    _recalculate()

    window.ShowDialog()
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ui = _load_uiutils()
    doc = revit.doc
    if doc is None:
        ui.uiUtils_alert("No active document.", title="Parking Demand Calculator")
        return

    rooms = _get_all_rooms(doc)
    if not rooms:
        ui.uiUtils_alert(
            "No rooms found in this model.\n\n"
            "Add rooms and set their Department parameter to use this tool.",
            title="Parking Demand Calculator",
        )
        return

    function_data = _build_function_data(rooms)
    actual_spots = _get_actual_parking_count(doc)

    _show_dialog(function_data, actual_spots)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            import WWP_uiUtils as ui
            ui.uiUtils_alert(traceback.format_exc(), title="Parking Demand Calculator")
        except Exception:
            from pyrevit import forms
            forms.alert(traceback.format_exc(), title="Parking Demand Calculator")
