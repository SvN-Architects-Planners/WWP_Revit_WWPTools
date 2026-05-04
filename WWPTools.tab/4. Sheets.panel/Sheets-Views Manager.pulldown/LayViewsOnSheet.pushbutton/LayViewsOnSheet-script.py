# -*- coding: utf-8 -*-

import ast
import os
import sys

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import revit, DB

from System.IO import File
from System.Windows import CornerRadius, RoutedEventHandler, TextAlignment, TextWrapping, Thickness, VerticalAlignment
from System.Windows.Controls import Border, CheckBox, ListBoxItem, SelectionChangedEventHandler, TextBlock, TextChangedEventHandler
from System.Windows.Input import Cursors, MouseButtonEventHandler, MouseButtonState, MouseEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import Brushes


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from WWP_settings import get_tool_settings
import WWP_uiUtils as ui
from WWP_versioning import apply_window_title


doc = revit.doc
uidoc = revit.uidoc
config, save_config = get_tool_settings("LayViewsOnSheet", doc=doc)

CONFIG_LAST_TITLEBLOCK_ID = "last_titleblock_id"
CONFIG_LAST_GAP_MM = "last_gap_mm"

DEFAULT_GAP_MM = 12.0
DEFAULT_SHEET_NAME = "Viewport Layout"
DEFAULT_SHEET_PREFIX = "LAYOUT-"
PREVIEW_MARGIN = 24.0

_WPFUI_THEME_READY = False




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def _read_bundle_title():
    bundle_path = os.path.join(script_dir, "bundle.yaml")
    if not os.path.isfile(bundle_path):
        return "Lay Views on Sheet"
    try:
        with open(bundle_path, "r") as bundle_file:
            for raw_line in bundle_file:
                line = raw_line.strip()
                if not line.lower().startswith("title:"):
                    continue
                value = line.split(":", 1)[1].strip()
                if not value:
                    break
                try:
                    parsed = ast.literal_eval(value)
                    if parsed:
                        return str(parsed)
                except Exception:
                    return value.strip("\"'")
    except Exception:
        pass
    return "Lay Views on Sheet"


BUNDLE_TITLE = _read_bundle_title()
WINDOW_TITLE = " ".join(BUNDLE_TITLE.splitlines()).strip() or "Lay Views on Sheet"


def ensure_wpfui_theme():
    global _WPFUI_THEME_READY
    if _WPFUI_THEME_READY:
        return

    try:
        revit_version = int(str(__revit__.Application.VersionNumber))
    except Exception:
        revit_version = None

    dll_name = "WWPTools.WpfUI.net8.0-windows.dll" if revit_version and revit_version >= 2025 else "WWPTools.WpfUI.net48.dll"
    dll_path = os.path.join(lib_path, dll_name)
    if not os.path.isfile(dll_path):
        return

    try:
        if hasattr(clr, "AddReferenceToFileAndPath"):
            clr.AddReferenceToFileAndPath(dll_path)
        else:
            clr.AddReference(dll_path)
        _WPFUI_THEME_READY = True
    except Exception:
        pass


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _element_id_value(elem_id):
    if elem_id is None:
        return None
    if hasattr(elem_id, "IntegerValue"):
        return _elem_id_int(elem_id)
    if hasattr(elem_id, "Value"):
        return elem_id.Value
    try:
        return int(elem_id)
    except Exception:
        return None


def _normalize_name(value):
    return " ".join(str(value or "").split()).strip()


def _view_display_name(view):
    try:
        return "{} [{}]".format(view.Name, view.ViewType)
    except Exception:
        return "<view>"


def _view_identity(view):
    return _element_id_value(getattr(view, "Id", None))


def _is_selectable_view(view):
    if view is None:
        return False
    try:
        if view.IsTemplate:
            return False
    except Exception:
        return False
    if isinstance(view, DB.ViewSheet):
        return False
    if isinstance(view, DB.ViewSchedule):
        return False
    try:
        if not view.CanBePrinted:
            return False
    except Exception:
        return False
    try:
        rejected = {
            "DrawingSheet",
            "Schedule",
            "ProjectBrowser",
            "SystemBrowser",
            "Internal",
            "Undefined",
            "Report",
            "CostReport",
            "LoadsReport",
            "ColumnSchedule",
            "PanelSchedule",
        }
        if str(view.ViewType) in rejected:
            return False
    except Exception:
        pass
    return True


def _collect_candidate_views():
    views = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
        if _is_selectable_view(view):
            views.append(view)
    return sorted(views, key=lambda item: (_normalize_name(str(item.ViewType)).lower(), _normalize_name(item.Name).lower()))


def _selected_views_from_ui():
    selected = []
    seen = set()
    for element_id in uidoc.Selection.GetElementIds():
        element = doc.GetElement(element_id)
        view = None
        if isinstance(element, DB.View):
            view = element
        elif isinstance(element, DB.Viewport):
            try:
                view = doc.GetElement(element.ViewId)
            except Exception:
                view = None
        if not _is_selectable_view(view):
            continue
        view_id = _element_id_value(view.Id)
        if view_id in seen:
            continue
        seen.add(view_id)
        selected.append(view)
    return selected


def _collect_titleblock_types():
    types = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsElementType()
        .ToElements()
    )
    return sorted(types, key=lambda item: "{}: {}".format(_normalize_name(getattr(item, "FamilyName", "")), _normalize_name(getattr(item, "Name", ""))).lower())


def _titleblock_display_name(titleblock_type):
    try:
        family_name = _normalize_name(titleblock_type.FamilyName)
    except Exception:
        family_name = ""
    try:
        type_name = _normalize_name(titleblock_type.Name)
    except Exception:
        type_name = ""
    if family_name and type_name:
        return "{} : {}".format(family_name, type_name)
    return family_name or type_name or "<titleblock>"


def _generate_default_sheet_number():
    existing = set()
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if sheet.SheetNumber:
                existing.add(sheet.SheetNumber.strip().upper())
        except Exception:
            continue
    for index in range(1, 10000):
        candidate = "{}{:03d}".format(DEFAULT_SHEET_PREFIX, index)
        if candidate.upper() not in existing:
            return candidate
    return "{}{}".format(DEFAULT_SHEET_PREFIX, doc.GetHashCode())


def _as_float(value, default_value):
    try:
        return float(str(value).strip())
    except Exception:
        return float(default_value)


def _mm_to_feet(value_mm):
    return _as_float(value_mm, DEFAULT_GAP_MM) / 304.8


def _outline_points(outline):
    outline_min = getattr(outline, "Min", None) or getattr(outline, "MinimumPoint", None) or getattr(outline, "Minimum", None)
    outline_max = getattr(outline, "Max", None) or getattr(outline, "MaximumPoint", None) or getattr(outline, "Maximum", None)
    return outline_min, outline_max


def _get_titleblock_bounds(sheet):
    titleblocks = (
        DB.FilteredElementCollector(doc, sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for titleblock in titleblocks:
        try:
            bbox = titleblock.get_BoundingBox(sheet) or titleblock.get_BoundingBox(None)
            if bbox:
                return bbox.Min, bbox.Max
        except Exception:
            continue
    outline = sheet.Outline
    return (
        DB.XYZ(outline.Min.U, outline.Min.V, 0.0),
        DB.XYZ(outline.Max.U, outline.Max.V, 0.0),
    )


def _viewport_outline_size(viewport):
    try:
        outline = viewport.GetBoxOutline()
    except Exception:
        return 1.0, 1.0
    outline_min, outline_max = _outline_points(outline)
    if outline_min is None or outline_max is None:
        return 1.0, 1.0
    width = max(outline_max.X - outline_min.X, 1.0 / 12.0)
    height = max(outline_max.Y - outline_min.Y, 1.0 / 12.0)
    return width, height


def _clamp_center_by_size(target_center, width, height, bounds_min, bounds_max):
    half_w = max(width / 2.0, 0.0)
    half_h = max(height / 2.0, 0.0)
    available_w = bounds_max.X - bounds_min.X
    available_h = bounds_max.Y - bounds_min.Y

    if available_w < 2.0 * half_w:
        center_x = (bounds_min.X + bounds_max.X) / 2.0
    else:
        center_x = max(bounds_min.X + half_w, min(target_center.X, bounds_max.X - half_w))

    if available_h < 2.0 * half_h:
        center_y = (bounds_min.Y + bounds_max.Y) / 2.0
    else:
        center_y = max(bounds_min.Y + half_h, min(target_center.Y, bounds_max.Y - half_h))

    return DB.XYZ(center_x, center_y, 0.0)


def _clamp_viewport_center(viewport, target_center, bounds_min, bounds_max):
    try:
        outline = viewport.GetBoxOutline()
    except Exception:
        viewport.SetBoxCenter(DB.XYZ(target_center.X, target_center.Y, 0.0))
        return
    outline_min, outline_max = _outline_points(outline)
    if outline_min is None or outline_max is None:
        viewport.SetBoxCenter(DB.XYZ(target_center.X, target_center.Y, 0.0))
        return
    width = max(outline_max.X - outline_min.X, 1.0 / 12.0)
    height = max(outline_max.Y - outline_min.Y, 1.0 / 12.0)
    center = _clamp_center_by_size(target_center, width, height, bounds_min, bounds_max)
    viewport.SetBoxCenter(DB.XYZ(center.X, center.Y, 0.0))


def _measure_views_for_titleblock(titleblock_type, views):
    group = DB.TransactionGroup(doc, WINDOW_TITLE)
    transaction = None
    try:
        group.Start()
        transaction = DB.Transaction(doc, "Measure Viewports")
        transaction.Start()
        sheet = DB.ViewSheet.Create(doc, titleblock_type.Id)
        doc.Regenerate()
        bounds_min, bounds_max = _get_titleblock_bounds(sheet)
        placed = []
        unplaceable = []
        x_cursor = bounds_min.X + 1.0
        y_base = bounds_min.Y + 1.0
        for index, view in enumerate(views):
            can_add = False
            try:
                can_add = DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id)
            except Exception:
                can_add = False
            if not can_add:
                unplaceable.append(view)
                continue
            temp_point = DB.XYZ(x_cursor, y_base + (index * 0.25), 0.0)
            try:
                viewport = DB.Viewport.Create(doc, sheet.Id, view.Id, temp_point)
                placed.append((view, viewport))
                x_cursor += 10.0
            except Exception:
                unplaceable.append(view)
        doc.Regenerate()
        size_items = []
        for view, viewport in placed:
            width, height = _viewport_outline_size(viewport)
            size_items.append({"view": view, "width": width, "height": height})
        transaction.Commit()
        group.RollBack()
        return bounds_min, bounds_max, size_items, unplaceable
    except Exception:
        try:
            if transaction and transaction.HasStarted():
                transaction.RollBack()
        except Exception:
            pass
        try:
            if group.HasStarted():
                group.RollBack()
        except Exception:
            pass
        raise


def _auto_layout_positions(bounds_min, bounds_max, items, gap_feet):
    gap = max(gap_feet, 0.0)
    x_cursor = bounds_min.X + gap
    y_top = bounds_max.Y - gap
    row_height = 0.0
    positions = []
    for item in items:
        width = max(item.get("width", 0.0), 1.0 / 12.0)
        height = max(item.get("height", 0.0), 1.0 / 12.0)
        if positions and x_cursor + width > bounds_max.X - gap:
            x_cursor = bounds_min.X + gap
            y_top -= row_height + gap
            row_height = 0.0
        center = DB.XYZ(x_cursor + (width / 2.0), y_top - (height / 2.0), 0.0)
        center = _clamp_center_by_size(center, width, height, bounds_min, bounds_max)
        positions.append(center)
        x_cursor += width + gap
        row_height = max(row_height, height)
    return positions


class LayoutPreviewWindow(object):
    def __init__(self, all_views, preselected_view_ids, titleblocks, last_titleblock_id, last_gap_mm):
        self.all_views = list(all_views or [])
        self.titleblocks = list(titleblocks or [])
        self.result = None
        self._titleblock_lookup = {}
        self._measurement_cache = {}
        self._layout_items = []
        self._view_records = []
        self._loading_view_list = False
        self._bounds_min = None
        self._bounds_max = None
        self._preview_state = None
        self._drag_item = None
        self._drag_start_mouse = None
        self._drag_start_center = None

        ensure_wpfui_theme()
        xaml_path = os.path.join(script_dir, "LayViewsOnSheetWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._txt_sheet_number = self.window.FindName("TxtSheetNumber")
        self._txt_sheet_name = self.window.FindName("TxtSheetName")
        self._cmb_titleblock = self.window.FindName("CmbTitleblock")
        self._txt_gap_mm = self.window.FindName("TxtGapMm")
        self._txt_view_search = self.window.FindName("TxtViewSearch")
        self._views_list = self.window.FindName("ViewsList")
        self._btn_select_visible = self.window.FindName("BtnSelectVisible")
        self._btn_clear_visible = self.window.FindName("BtnClearVisible")
        self._txt_view_summary = self.window.FindName("TxtViewSummary")
        self._txt_warning = self.window.FindName("TxtWarning")
        self._footer_status = self.window.FindName("FooterStatus")
        self._btn_auto_layout = self.window.FindName("BtnAutoLayout")
        self._btn_create = self.window.FindName("BtnCreate")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._preview_host = self.window.FindName("PreviewHost")
        self._preview_canvas = self.window.FindName("PreviewCanvas")

        if self._header_title is not None:
            self._header_title.Text = WINDOW_TITLE
        if self._header_subtitle is not None:
            self._header_subtitle.Text = "Create one new sheet from selected views. Drag the grey boxes to adjust viewport locations before placing them."

        self._txt_sheet_number.Text = _generate_default_sheet_number()
        self._txt_sheet_name.Text = DEFAULT_SHEET_NAME
        self._txt_gap_mm.Text = str(_as_float(last_gap_mm, DEFAULT_GAP_MM)).rstrip("0").rstrip(".")
        self._txt_view_search.Text = ""

        self._populate_titleblocks(last_titleblock_id)
        self._build_view_records(preselected_view_ids)
        self._rebuild_views_list()

        self._cmb_titleblock.SelectionChanged += SelectionChangedEventHandler(self._on_titleblock_changed)
        self._btn_auto_layout.Click += RoutedEventHandler(self._on_auto_layout)
        self._btn_select_visible.Click += RoutedEventHandler(self._on_select_visible)
        self._btn_clear_visible.Click += RoutedEventHandler(self._on_clear_visible)
        self._btn_create.Click += RoutedEventHandler(self._on_create)
        self._btn_cancel.Click += RoutedEventHandler(self._on_cancel)
        self._txt_view_search.TextChanged += TextChangedEventHandler(self._on_search_changed)
        self._preview_host.SizeChanged += self._on_preview_size_changed

        self._refresh_measurement(reset_layout=True)

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _build_view_records(self, preselected_view_ids):
        preselected = set(preselected_view_ids or [])
        for view in self.all_views:
            self._view_records.append(
                {
                    "view": view,
                    "id": _view_identity(view),
                    "label": _view_display_name(view),
                    "selected": _view_identity(view) in preselected,
                    "visible": True,
                }
            )

    def _search_text(self):
        return _normalize_name(self._txt_view_search.Text).lower()

    def _filtered_records(self):
        return [record for record in self._view_records if record.get("visible")]

    def _selected_views(self):
        selected = []
        for record in self._view_records:
            if record.get("selected"):
                selected.append(record["view"])
        return selected

    def _visible_selected_count(self):
        return len([record for record in self._view_records if record.get("visible") and record.get("selected")])

    def _make_view_item(self, record):
        item = ListBoxItem()
        checkbox = CheckBox()
        checkbox.Tag = record
        checkbox.Content = record["label"]
        checkbox.IsChecked = bool(record.get("selected"))
        checkbox.VerticalAlignment = VerticalAlignment.Center
        checkbox.Checked += RoutedEventHandler(self._on_view_checked)
        checkbox.Unchecked += RoutedEventHandler(self._on_view_checked)
        item.Content = checkbox
        item.Tag = record
        return item

    def _rebuild_views_list(self):
        filter_text = self._search_text()
        self._loading_view_list = True
        self._views_list.Items.Clear()
        for record in self._view_records:
            label = record["label"].lower()
            record["visible"] = not filter_text or filter_text in label
            if record["visible"]:
                self._views_list.Items.Add(self._make_view_item(record))
        self._loading_view_list = False
        self._update_summary([])

    def _populate_titleblocks(self, selected_type_id):
        self._cmb_titleblock.Items.Clear()
        selected_label = None
        selected_id_text = str(selected_type_id or "").strip()
        for titleblock in self.titleblocks:
            label = _titleblock_display_name(titleblock)
            self._cmb_titleblock.Items.Add(label)
            self._titleblock_lookup[label] = titleblock
            if selected_id_text and str(_element_id_value(titleblock.Id)) == selected_id_text:
                selected_label = label
        if selected_label:
            self._cmb_titleblock.SelectedItem = selected_label
        elif self._cmb_titleblock.Items.Count > 0:
            self._cmb_titleblock.SelectedIndex = 0

    def _selected_titleblock(self):
        item = self._cmb_titleblock.SelectedItem
        if item is None:
            return None
        return self._titleblock_lookup.get(str(item))

    def _selected_gap_feet(self):
        return _mm_to_feet(self._txt_gap_mm.Text)

    def _clone_layout_items(self, size_items):
        items = []
        for size_item in size_items:
            view = size_item["view"]
            items.append(
                {
                    "view": view,
                    "label": _view_display_name(view),
                    "width": size_item["width"],
                    "height": size_item["height"],
                    "center": DB.XYZ(0.0, 0.0, 0.0),
                    "visual": None,
                }
            )
        return items

    def _measurement_data(self):
        titleblock = self._selected_titleblock()
        if titleblock is None:
            return None
        selected_views = self._selected_views()
        selected_ids = tuple(sorted([_view_identity(view) for view in selected_views if _view_identity(view) is not None]))
        if not selected_ids:
            return {
                "bounds_min": None,
                "bounds_max": None,
                "size_items": [],
                "unplaceable": [],
            }
        type_id = _element_id_value(titleblock.Id)
        cache_key = (type_id, selected_ids)
        if cache_key not in self._measurement_cache:
            bounds_min, bounds_max, size_items, unplaceable = _measure_views_for_titleblock(titleblock, selected_views)
            self._measurement_cache[cache_key] = {
                "bounds_min": bounds_min,
                "bounds_max": bounds_max,
                "size_items": size_items,
                "unplaceable": list(unplaceable),
            }
        return self._measurement_cache[cache_key]

    def _refresh_measurement(self, reset_layout):
        data = self._measurement_data()
        if data is None:
            self._layout_items = []
            self._bounds_min = None
            self._bounds_max = None
            self._txt_warning.Text = "Select a titleblock to continue."
            self._render_preview()
            return
        if data["bounds_min"] is None or data["bounds_max"] is None:
            self._layout_items = []
            self._bounds_min = None
            self._bounds_max = None
            self._update_summary([])
            self._render_preview()
            return
        self._bounds_min = data["bounds_min"]
        self._bounds_max = data["bounds_max"]
        self._layout_items = self._clone_layout_items(data["size_items"])
        if reset_layout:
            positions = _auto_layout_positions(self._bounds_min, self._bounds_max, self._layout_items, self._selected_gap_feet())
            for item, center in zip(self._layout_items, positions):
                item["center"] = center
        self._update_summary(data["unplaceable"])
        self._render_preview()

    def _update_summary(self, unplaceable):
        requested_count = len(self._selected_views())
        visible_count = len(self._filtered_records())
        visible_selected = self._visible_selected_count()
        placeable_count = len(self._layout_items)
        skipped_count = len(unplaceable or [])
        self._txt_view_summary.Text = "{} selected, {} visible in the filter, {} ready to place on the new sheet.".format(requested_count, visible_count if self._search_text() else len(self._view_records), placeable_count)
        if self._search_text():
            self._footer_status.Text = "{} visible view(s) currently checked in the filter.".format(visible_selected)
        else:
            self._footer_status.Text = "Drag boxes to refine placement. Create Sheet uses the current preview positions."
        if skipped_count:
            names = ", ".join([_normalize_name(view.Name) for view in list(unplaceable)[:4]])
            extra = ""
            if skipped_count > 4:
                extra = " and {} more".format(skipped_count - 4)
            self._txt_warning.Text = "{} view(s) cannot be placed on a new sheet: {}{}".format(skipped_count, names, extra)
        elif requested_count == 0:
            self._txt_warning.Text = "Select at least one view to build the sheet preview."
        else:
            self._txt_warning.Text = ""
        self._btn_create.IsEnabled = placeable_count > 0

    def _render_preview(self):
        self._preview_canvas.Children.Clear()
        width = max(float(self._preview_host.ActualWidth) - 8.0, 300.0)
        height = max(float(self._preview_host.ActualHeight) - 8.0, 240.0)
        self._preview_canvas.Width = width
        self._preview_canvas.Height = height

        if self._bounds_min is None or self._bounds_max is None:
            return

        sheet_width = max(self._bounds_max.X - self._bounds_min.X, 1.0)
        sheet_height = max(self._bounds_max.Y - self._bounds_min.Y, 1.0)
        scale = min((width - (2.0 * PREVIEW_MARGIN)) / sheet_width, (height - (2.0 * PREVIEW_MARGIN)) / sheet_height)
        if scale <= 0.0:
            return

        scaled_width = sheet_width * scale
        scaled_height = sheet_height * scale
        origin_x = (width - scaled_width) / 2.0
        origin_y = (height - scaled_height) / 2.0
        self._preview_state = {
            "origin_x": origin_x,
            "origin_y": origin_y,
            "scale": scale,
            "canvas_width": width,
            "canvas_height": height,
        }

        sheet_border = Border()
        sheet_border.Background = Brushes.WhiteSmoke
        sheet_border.BorderBrush = Brushes.DimGray
        sheet_border.BorderThickness = Thickness(1)
        sheet_border.CornerRadius = CornerRadius(0)
        sheet_border.IsHitTestVisible = False
        self._preview_canvas.Children.Add(sheet_border)
        from System.Windows.Controls import Canvas
        Canvas.SetLeft(sheet_border, origin_x)
        Canvas.SetTop(sheet_border, origin_y)
        sheet_border.Width = scaled_width
        sheet_border.Height = scaled_height

        for item in self._layout_items:
            visual = self._build_preview_box(item)
            item["visual"] = visual
            self._preview_canvas.Children.Add(visual)
            self._update_preview_box(item)

    def _sheet_rect_for_item(self, item):
        scale = self._preview_state["scale"]
        origin_x = self._preview_state["origin_x"]
        origin_y = self._preview_state["origin_y"]
        left = origin_x + ((item["center"].X - self._bounds_min.X) * scale) - ((item["width"] * scale) / 2.0)
        top = origin_y + ((self._bounds_max.Y - item["center"].Y) * scale) - ((item["height"] * scale) / 2.0)
        width = max(item["width"] * scale, 28.0)
        height = max(item["height"] * scale, 20.0)
        return left, top, width, height

    def _build_preview_box(self, item):
        border = Border()
        border.Tag = item
        border.Background = Brushes.Gainsboro
        border.BorderBrush = Brushes.Gray
        border.BorderThickness = Thickness(1)
        border.CornerRadius = CornerRadius(4)
        border.Cursor = Cursors.SizeAll
        border.ToolTip = item["label"]
        label = TextBlock()
        label.Text = _normalize_name(item["view"].Name)
        label.TextWrapping = TextWrapping.Wrap
        label.TextAlignment = TextAlignment.Center
        label.Margin = Thickness(8)
        label.Foreground = Brushes.Black
        border.Child = label
        border.MouseLeftButtonDown += MouseButtonEventHandler(self._on_box_mouse_down)
        border.MouseMove += MouseEventHandler(self._on_box_mouse_move)
        border.MouseLeftButtonUp += MouseButtonEventHandler(self._on_box_mouse_up)
        return border

    def _update_preview_box(self, item):
        if item.get("visual") is None or self._preview_state is None:
            return
        left, top, width, height = self._sheet_rect_for_item(item)
        from System.Windows.Controls import Canvas
        Canvas.SetLeft(item["visual"], left)
        Canvas.SetTop(item["visual"], top)
        item["visual"].Width = width
        item["visual"].Height = height

    def _on_preview_size_changed(self, sender, event_args):
        self._render_preview()

    def _on_search_changed(self, sender, event_args):
        self._rebuild_views_list()

    def _on_view_checked(self, sender, event_args):
        if self._loading_view_list:
            return
        record = getattr(sender, "Tag", None)
        if record is None:
            return
        record["selected"] = bool(sender.IsChecked)
        self._refresh_measurement(reset_layout=True)

    def _on_select_visible(self, sender, event_args):
        for record in self._filtered_records():
            record["selected"] = True
        self._rebuild_views_list()
        self._refresh_measurement(reset_layout=True)

    def _on_clear_visible(self, sender, event_args):
        for record in self._filtered_records():
            record["selected"] = False
        self._rebuild_views_list()
        self._refresh_measurement(reset_layout=True)

    def _on_titleblock_changed(self, sender, event_args):
        self._refresh_measurement(reset_layout=True)

    def _on_auto_layout(self, sender, event_args):
        if not self._layout_items or self._bounds_min is None or self._bounds_max is None:
            return
        positions = _auto_layout_positions(self._bounds_min, self._bounds_max, self._layout_items, self._selected_gap_feet())
        for item, center in zip(self._layout_items, positions):
            item["center"] = center
        self._render_preview()

    def _on_box_mouse_down(self, sender, event_args):
        item = getattr(sender, "Tag", None)
        if item is None or self._preview_state is None:
            return
        self._drag_item = item
        self._drag_start_mouse = event_args.GetPosition(self._preview_canvas)
        self._drag_start_center = DB.XYZ(item["center"].X, item["center"].Y, 0.0)
        sender.CaptureMouse()
        sender.BorderBrush = Brushes.DimGray
        event_args.Handled = True

    def _on_box_mouse_move(self, sender, event_args):
        if self._drag_item is None or self._preview_state is None:
            return
        if event_args.LeftButton != MouseButtonState.Pressed:
            return
        if not sender.IsMouseCaptured:
            return
        current = event_args.GetPosition(self._preview_canvas)
        delta_x = (current.X - self._drag_start_mouse.X) / self._preview_state["scale"]
        delta_y = -(current.Y - self._drag_start_mouse.Y) / self._preview_state["scale"]
        target = DB.XYZ(self._drag_start_center.X + delta_x, self._drag_start_center.Y + delta_y, 0.0)
        item = self._drag_item
        item["center"] = _clamp_center_by_size(target, item["width"], item["height"], self._bounds_min, self._bounds_max)
        self._update_preview_box(item)
        event_args.Handled = True

    def _on_box_mouse_up(self, sender, event_args):
        if sender.IsMouseCaptured:
            sender.ReleaseMouseCapture()
        sender.BorderBrush = Brushes.Gray
        self._drag_item = None
        self._drag_start_mouse = None
        self._drag_start_center = None
        event_args.Handled = True

    def _on_cancel(self, sender, event_args):
        self.result = None
        self.window.DialogResult = False
        self.window.Close()

    def _on_create(self, sender, event_args):
        titleblock = self._selected_titleblock()
        if titleblock is None:
            self._txt_warning.Text = "Select a titleblock."
            return
        sheet_number = _normalize_name(self._txt_sheet_number.Text)
        if not sheet_number:
            self._txt_warning.Text = "Enter a sheet number."
            return
        existing_numbers = set()
        for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
            try:
                if sheet.SheetNumber:
                    existing_numbers.add(sheet.SheetNumber.strip().upper())
            except Exception:
                continue
        if sheet_number.upper() in existing_numbers:
            self._txt_warning.Text = "Sheet number already exists."
            return
        if not self._layout_items:
            self._txt_warning.Text = "No views can be placed on the new sheet."
            return
        self.result = {
            "sheet_number": sheet_number,
            "sheet_name": _normalize_name(self._txt_sheet_name.Text) or DEFAULT_SHEET_NAME,
            "titleblock": titleblock,
            "gap_mm": _as_float(self._txt_gap_mm.Text, DEFAULT_GAP_MM),
            "layout_items": [
                {"view": item["view"], "center": DB.XYZ(item["center"].X, item["center"].Y, 0.0)}
                for item in self._layout_items
            ],
        }
        self.window.DialogResult = True
        self.window.Close()


def _set_sheet_name(sheet, sheet_name):
    if not sheet_name:
        return
    try:
        sheet.Name = sheet_name
        return
    except Exception:
        pass
    param = sheet.LookupParameter("Sheet Name")
    if param and not param.IsReadOnly:
        try:
            param.Set(sheet_name)
        except Exception:
            pass


def _create_sheet_from_layout(result):
    titleblock = result["titleblock"]
    layout_items = list(result.get("layout_items", []))
    transaction = DB.Transaction(doc, WINDOW_TITLE)
    transaction.Start()
    try:
        sheet = DB.ViewSheet.Create(doc, titleblock.Id)
        sheet.SheetNumber = result["sheet_number"]
        _set_sheet_name(sheet, result.get("sheet_name", ""))
        created = []
        skipped = []
        for item in layout_items:
            view = item["view"]
            center = item["center"]
            can_add = False
            try:
                can_add = DB.Viewport.CanAddViewToSheet(doc, sheet.Id, view.Id)
            except Exception:
                can_add = False
            if not can_add:
                skipped.append(view)
                continue
            try:
                viewport = DB.Viewport.Create(doc, sheet.Id, view.Id, center)
                created.append((viewport, center, view))
            except Exception:
                skipped.append(view)
        if not created:
            raise Exception("No selected views could be placed on the new sheet.")
        doc.Regenerate()
        bounds_min, bounds_max = _get_titleblock_bounds(sheet)
        for viewport, center, view in created:
            _clamp_viewport_center(viewport, center, bounds_min, bounds_max)
        doc.Regenerate()
        transaction.Commit()
        return sheet, [view for _, _, view in created], skipped
    except Exception:
        transaction.RollBack()
        raise


def main():
    all_views = _collect_candidate_views()
    if not all_views:
        ui.uiUtils_alert("No placeable views found in this model.", title=WINDOW_TITLE)
        return

    preselected_view_ids = [_view_identity(view) for view in _selected_views_from_ui() if _view_identity(view) is not None]

    titleblocks = _collect_titleblock_types()
    if not titleblocks:
        ui.uiUtils_alert("No titleblock types found in this model.", title=WINDOW_TITLE)
        return

    last_titleblock_id = getattr(config, CONFIG_LAST_TITLEBLOCK_ID, None)
    last_gap_mm = getattr(config, CONFIG_LAST_GAP_MM, DEFAULT_GAP_MM)

    dialog = LayoutPreviewWindow(all_views, preselected_view_ids, titleblocks, last_titleblock_id, last_gap_mm)
    confirmed = dialog.ShowDialog()
    if not confirmed or not dialog.result:
        return

    try:
        sheet, created, skipped = _create_sheet_from_layout(dialog.result)
    except Exception as exc:
        ui.uiUtils_alert(str(exc), title=WINDOW_TITLE)
        return

    setattr(config, CONFIG_LAST_TITLEBLOCK_ID, str(_element_id_value(dialog.result["titleblock"].Id)))
    setattr(config, CONFIG_LAST_GAP_MM, dialog.result.get("gap_mm", DEFAULT_GAP_MM))
    save_config()

    message = "Created sheet {} with {} viewport(s).".format(sheet.SheetNumber, len(created))
    if skipped:
        message += "\n\nSkipped {} view(s) that could not be added: {}".format(
            len(skipped),
            ", ".join([_normalize_name(view.Name) for view in skipped[:5]]) + ("..." if len(skipped) > 5 else ""),
        )
    ui.uiUtils_alert(message, title=WINDOW_TITLE)


if __name__ == "__main__":
    main()
