# -*- coding: utf-8 -*-

import ast
import os
import sys
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import DB, revit

from System.IO import File
from System.Windows import CornerRadius, FontWeights, TextAlignment, TextWrapping, Thickness
from System.Windows.Controls import Border, StackPanel, TextBlock, TextBox
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
config, save_config = get_tool_settings("SheetDuplicator", doc=doc)

CONFIG_LAST_SOURCE_SHEET_ID = "last_source_sheet_id"
CONFIG_LAST_DUPLICATE_OPTION = "last_duplicate_option"
CONFIG_LAST_VIEW_PREFIX = "last_view_prefix"
CONFIG_LAST_VIEW_SUFFIX = "last_view_suffix"

DEFAULT_VIEW_SUFFIX = "-copy"
PREVIEW_MARGIN = 24.0
_WPFUI_THEME_READY = False

OPTION_LABELS = {
    "Duplicate": "Duplicate",
    "WithDetailing": "Duplicate with Details",
    "AsDependent": "Duplicate as Dependent",
}


def _read_bundle_title():
    bundle_path = os.path.join(script_dir, "bundle.yaml")
    if not os.path.isfile(bundle_path):
        return "Sheet Duplicator"
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
    return "Sheet Duplicator"


BUNDLE_TITLE = _read_bundle_title()
WINDOW_TITLE = " ".join(BUNDLE_TITLE.splitlines()).strip() or "Sheet Duplicator"


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


def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.IntegerValue)  # Revit 2023-


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


def _clean_name(value):
    return _normalize_name(value).replace("{", "").replace("}", "")


def _collect_existing_view_names():
    names = set()
    for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
        try:
            if view.Name:
                names.add(view.Name)
        except Exception:
            continue
    return names


def _ensure_unique_name(existing_names, desired_name):
    candidate = _normalize_name(desired_name) or "View Copy"
    if candidate not in existing_names:
        existing_names.add(candidate)
        return candidate
    index = 2
    while True:
        updated = "{} ({})".format(candidate, index)
        if updated not in existing_names:
            existing_names.add(updated)
            return updated
        index += 1


def _default_view_name(source_name, prefix, suffix):
    base = _clean_name(source_name)
    applied_suffix = suffix if suffix else DEFAULT_VIEW_SUFFIX
    return "{}{}{}".format(prefix or "", base, applied_suffix)


def _collect_existing_sheet_numbers():
    values = set()
    for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
        try:
            if sheet.SheetNumber:
                values.add(sheet.SheetNumber.strip().upper())
        except Exception:
            continue
    return values


def _default_sheet_number(sheet):
    base = "{}-COPY".format(_normalize_name(getattr(sheet, "SheetNumber", "")) or "COPY")
    existing = _collect_existing_sheet_numbers()
    if base.upper() not in existing:
        return base
    index = 2
    while True:
        candidate = "{}-{}".format(base, index)
        if candidate.upper() not in existing:
            return candidate
        index += 1


def _default_sheet_name(sheet):
    return "{} Copy".format(_normalize_name(getattr(sheet, "Name", "")) or "Sheet")


def _map_duplicate_option(key):
    mapping = {
        "Duplicate": DB.ViewDuplicateOption.Duplicate,
        "WithDetailing": DB.ViewDuplicateOption.WithDetailing,
        "AsDependent": DB.ViewDuplicateOption.AsDependent,
    }
    return mapping.get(key, DB.ViewDuplicateOption.WithDetailing)


def _option_display_name(key):
    return OPTION_LABELS.get(key, OPTION_LABELS["WithDetailing"])


def _can_duplicate_view(view, option_key):
    option = _map_duplicate_option(option_key)
    try:
        return bool(view.CanViewBeDuplicated(option))
    except Exception:
        return False


def _sheet_display_name(sheet, current_sheet_id):
    prefix = ""
    try:
        if current_sheet_id is not None and _elem_id_int(sheet.Id) == current_sheet_id:
            prefix = "[Current Sheet] "
    except Exception:
        prefix = ""
    return "{}{} - {}".format(prefix, sheet.SheetNumber or "", sheet.Name or "")


def _collect_source_sheets():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheets = sorted(sheets, key=lambda item: (_normalize_name(item.SheetNumber).lower(), _normalize_name(item.Name).lower()))
    current_sheet_id = None
    try:
        active_view = uidoc.ActiveView
        if isinstance(active_view, DB.ViewSheet):
            current_sheet_id = _elem_id_int(active_view.Id)
    except Exception:
        current_sheet_id = None
    if current_sheet_id is not None:
        current = [sheet for sheet in sheets if _element_id_value(sheet.Id) == current_sheet_id]
        others = [sheet for sheet in sheets if _element_id_value(sheet.Id) != current_sheet_id]
        sheets = current + others
    return sheets, current_sheet_id


def _get_titleblock_type_id(sheet):
    titleblocks = (
        DB.FilteredElementCollector(doc, sheet.Id)
        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    if titleblocks:
        try:
            return titleblocks[0].GetTypeId()
        except Exception:
            pass
    return DB.ElementId.InvalidElementId


def _get_titleblock_name(sheet):
    type_id = _get_titleblock_type_id(sheet)
    titleblock_type = doc.GetElement(type_id) if type_id and type_id != DB.ElementId.InvalidElementId else None
    if titleblock_type is None:
        return "No titleblock"
    family_name = _normalize_name(getattr(titleblock_type, "FamilyName", ""))
    type_name = _normalize_name(getattr(titleblock_type, "Name", ""))
    if family_name and type_name:
        return "{} : {}".format(family_name, type_name)
    return family_name or type_name or "Titleblock"


def _get_sheet_bounds(sheet):
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
    return DB.XYZ(outline.Min.U, outline.Min.V, 0.0), DB.XYZ(outline.Max.U, outline.Max.V, 0.0)


def _outline_points(outline):
    outline_min = getattr(outline, "Min", None) or getattr(outline, "MinimumPoint", None) or getattr(outline, "Minimum", None)
    outline_max = getattr(outline, "Max", None) or getattr(outline, "MaximumPoint", None) or getattr(outline, "Maximum", None)
    return outline_min, outline_max


def _viewport_size(viewport):
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


def _collect_schedule_slots(sheet):
    slots = []
    for instance in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.ScheduleSheetInstance):
        try:
            schedule = doc.GetElement(instance.ScheduleId)
        except Exception:
            schedule = None
        if schedule is None:
            continue
        try:
            if instance.IsTitleblockRevisionSchedule:
                continue
        except Exception:
            pass
        slots.append({"view": schedule, "point": instance.Point, "name": _normalize_name(getattr(schedule, "Name", ""))})
    return slots


def _collect_viewport_slots(sheet, option_key, prefix, suffix):
    slots = []
    for viewport in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport):
        try:
            view = doc.GetElement(viewport.ViewId)
        except Exception:
            view = None
        if view is None:
            continue
        center = viewport.GetBoxCenter()
        width, height = _viewport_size(viewport)
        can_duplicate = _can_duplicate_view(view, option_key)
        target_name = _default_view_name(view.Name, prefix, suffix) if can_duplicate else _normalize_name(view.Name)
        mode_note = "Reuse source view on the new sheet." if not can_duplicate else "Duplicate and rename this view."
        slots.append(
            {
                "source_view": view,
                "source_name": _normalize_name(view.Name),
                "center": DB.XYZ(center.X, center.Y, 0.0),
                "width": width,
                "height": height,
                "can_duplicate": can_duplicate,
                "target_name": target_name,
                "mode_note": mode_note,
                "text_box": None,
                "preview_text": None,
            }
        )
    slots.sort(key=lambda item: (-item["center"].Y, item["center"].X))
    return slots


class SheetDuplicatorDialog(object):
    def __init__(self, sheets, current_sheet_id):
        self.sheets = list(sheets or [])
        self.current_sheet_id = current_sheet_id
        self.result = None
        self._sheet_lookup = {}
        self._option_lookup = {}
        self._viewport_slots = []
        self._schedule_slots = []
        self._bounds_min = None
        self._bounds_max = None
        self._preview_state = None
        self._loading = True

        ensure_wpfui_theme()
        ui.uiUtils_ensure_theme()
        xaml_text = File.ReadAllText(os.path.join(script_dir, "SheetDuplicatorWindow.xaml"))
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._cmb_source_sheet = self.window.FindName("CmbSourceSheet")
        self._txt_titleblock = self.window.FindName("TxtTitleblock")
        self._txt_sheet_number = self.window.FindName("TxtSheetNumber")
        self._txt_sheet_name = self.window.FindName("TxtSheetName")
        self._cmb_duplicate_option = self.window.FindName("CmbDuplicateOption")
        self._txt_view_prefix = self.window.FindName("TxtViewPrefix")
        self._txt_view_suffix = self.window.FindName("TxtViewSuffix")
        self._assignments_host = self.window.FindName("AssignmentsHost")
        self._btn_reset_names = self.window.FindName("BtnResetNames")
        self._txt_summary = self.window.FindName("TxtSummary")
        self._txt_warning = self.window.FindName("TxtWarning")
        self._footer_status = self.window.FindName("FooterStatus")
        self._preview_host = self.window.FindName("PreviewHost")
        self._preview_canvas = self.window.FindName("PreviewCanvas")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._btn_create = self.window.FindName("BtnCreate")

        self._header_title.Text = WINDOW_TITLE
        self._header_subtitle.Text = "Duplicate one source sheet with the same viewport locations. Each viewport can get a newly duplicated view name before the new sheet is created."

        self._txt_view_prefix.Text = getattr(config, CONFIG_LAST_VIEW_PREFIX, "") or ""
        self._txt_view_suffix.Text = getattr(config, CONFIG_LAST_VIEW_SUFFIX, DEFAULT_VIEW_SUFFIX) or DEFAULT_VIEW_SUFFIX

        self._populate_source_sheets()
        self._populate_duplicate_options()

        self._cmb_source_sheet.SelectionChanged += self._on_source_sheet_changed
        self._cmb_duplicate_option.SelectionChanged += self._on_duplicate_option_changed
        self._btn_reset_names.Click += self._on_reset_names
        self._btn_cancel.Click += self._on_cancel
        self._btn_create.Click += self._on_create
        self._preview_host.SizeChanged += self._on_preview_size_changed

        self._loading = False
        self._load_source_sheet(reset_names=True, reset_sheet_identity=True)

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _populate_source_sheets(self):
        selected_sheet_id = str(getattr(config, CONFIG_LAST_SOURCE_SHEET_ID, "") or "").strip()
        self._cmb_source_sheet.Items.Clear()
        selected_label = None
        for sheet in self.sheets:
            label = _sheet_display_name(sheet, self.current_sheet_id)
            self._cmb_source_sheet.Items.Add(label)
            self._sheet_lookup[label] = sheet
            if selected_sheet_id and str(_element_id_value(sheet.Id)) == selected_sheet_id:
                selected_label = label
        if selected_label:
            self._cmb_source_sheet.SelectedItem = selected_label
        elif self._cmb_source_sheet.Items.Count > 0:
            self._cmb_source_sheet.SelectedIndex = 0

    def _populate_duplicate_options(self):
        selected_key = str(getattr(config, CONFIG_LAST_DUPLICATE_OPTION, "WithDetailing") or "WithDetailing")
        self._cmb_duplicate_option.Items.Clear()
        selected_label = None
        for key in ["Duplicate", "WithDetailing", "AsDependent"]:
            label = _option_display_name(key)
            self._cmb_duplicate_option.Items.Add(label)
            self._option_lookup[label] = key
            if key == selected_key:
                selected_label = label
        if selected_label:
            self._cmb_duplicate_option.SelectedItem = selected_label
        elif self._cmb_duplicate_option.Items.Count > 0:
            self._cmb_duplicate_option.SelectedIndex = 1

    def _selected_sheet(self):
        item = self._cmb_source_sheet.SelectedItem
        if item is None:
            return None
        return self._sheet_lookup.get(str(item))

    def _selected_option_key(self):
        item = self._cmb_duplicate_option.SelectedItem
        if item is None:
            return "WithDetailing"
        return self._option_lookup.get(str(item), "WithDetailing")

    def _load_source_sheet(self, reset_names, reset_sheet_identity):
        source_sheet = self._selected_sheet()
        if source_sheet is None:
            self._txt_warning.Text = "Select a source sheet."
            self._viewport_slots = []
            self._schedule_slots = []
            self._render_preview()
            return

        self._txt_titleblock.Text = _get_titleblock_name(source_sheet)
        self._bounds_min, self._bounds_max = _get_sheet_bounds(source_sheet)
        self._schedule_slots = _collect_schedule_slots(source_sheet)
        self._viewport_slots = _collect_viewport_slots(
            source_sheet,
            self._selected_option_key(),
            self._txt_view_prefix.Text,
            self._txt_view_suffix.Text,
        )

        if reset_sheet_identity:
            self._txt_sheet_number.Text = _default_sheet_number(source_sheet)
            self._txt_sheet_name.Text = _default_sheet_name(source_sheet)
        if reset_names:
            self._reset_target_names()
        else:
            for slot in self._viewport_slots:
                if slot["can_duplicate"] and not slot["target_name"]:
                    slot["target_name"] = _default_view_name(slot["source_name"], self._txt_view_prefix.Text, self._txt_view_suffix.Text)

        self._rebuild_assignment_rows()
        self._update_summary()
        self._render_preview()

    def _reset_target_names(self):
        existing_names = _collect_existing_view_names()
        generated_names = set(existing_names)
        for slot in self._viewport_slots:
            if slot["can_duplicate"]:
                slot["target_name"] = _ensure_unique_name(
                    generated_names,
                    _default_view_name(slot["source_name"], self._txt_view_prefix.Text, self._txt_view_suffix.Text),
                )
            else:
                slot["target_name"] = slot["source_name"]

    def _assignment_title_text(self, slot, index):
        return "{}. {}".format(index + 1, slot["source_name"])

    def _rebuild_assignment_rows(self):
        self._assignments_host.Children.Clear()
        for index, slot in enumerate(self._viewport_slots):
            box = Border()
            box.Margin = Thickness(0, 0, 0, 8)
            box.Padding = Thickness(10)
            box.CornerRadius = CornerRadius(6)
            box.BorderBrush = Brushes.Gainsboro
            box.BorderThickness = Thickness(1)
            box.Background = Brushes.White

            panel = StackPanel()
            title = TextBlock()
            title.Text = self._assignment_title_text(slot, index)
            title.FontWeight = FontWeights.SemiBold
            title.TextWrapping = TextWrapping.Wrap
            panel.Children.Add(title)

            note = TextBlock()
            note.Margin = Thickness(0, 4, 0, 8)
            note.Text = slot["mode_note"]
            note.Foreground = Brushes.DimGray
            note.TextWrapping = TextWrapping.Wrap
            panel.Children.Add(note)

            target_box = TextBox()
            target_box.Tag = slot
            target_box.Text = slot["target_name"]
            target_box.IsEnabled = bool(slot["can_duplicate"])
            target_box.TextChanged += self._on_target_name_changed
            panel.Children.Add(target_box)
            slot["text_box"] = target_box

            box.Child = panel
            self._assignments_host.Children.Add(box)

    def _update_summary(self):
        duplicate_count = len([slot for slot in self._viewport_slots if slot["can_duplicate"]])
        reused_count = len(self._viewport_slots) - duplicate_count
        schedule_count = len(self._schedule_slots)
        self._txt_summary.Text = "{} viewport(s): {} duplicated, {} reused. {} schedule instance(s) will be copied in place.".format(
            len(self._viewport_slots),
            duplicate_count,
            reused_count,
            schedule_count,
        )
        self._footer_status.Text = "The new sheet keeps the source viewport centers. Preview boxes update as target names change."
        self._btn_create.IsEnabled = self._selected_sheet() is not None
        if not self._viewport_slots and not self._schedule_slots:
            self._txt_warning.Text = "The selected source sheet has no placed viewports or schedules."
        else:
            self._txt_warning.Text = ""

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
        self._preview_state = {"origin_x": origin_x, "origin_y": origin_y, "scale": scale}

        from System.Windows.Controls import Canvas

        sheet_border = Border()
        sheet_border.Background = Brushes.WhiteSmoke
        sheet_border.BorderBrush = Brushes.DimGray
        sheet_border.BorderThickness = Thickness(1)
        sheet_border.CornerRadius = CornerRadius(0)
        sheet_border.IsHitTestVisible = False
        self._preview_canvas.Children.Add(sheet_border)
        Canvas.SetLeft(sheet_border, origin_x)
        Canvas.SetTop(sheet_border, origin_y)
        sheet_border.Width = scaled_width
        sheet_border.Height = scaled_height

        for slot in self._viewport_slots:
            border = Border()
            border.Background = Brushes.Gainsboro if slot["can_duplicate"] else Brushes.LightSteelBlue
            border.BorderBrush = Brushes.Gray
            border.BorderThickness = Thickness(1)
            border.CornerRadius = CornerRadius(4)

            text = TextBlock()
            text.Text = slot["target_name"] if slot["can_duplicate"] else slot["source_name"]
            text.Margin = Thickness(8)
            text.TextWrapping = TextWrapping.Wrap
            text.TextAlignment = TextAlignment.Center
            text.Foreground = Brushes.Black
            border.Child = text
            slot["preview_text"] = text

            left, top, box_width, box_height = self._sheet_rect_for_slot(slot)
            Canvas.SetLeft(border, left)
            Canvas.SetTop(border, top)
            border.Width = box_width
            border.Height = box_height
            self._preview_canvas.Children.Add(border)

    def _sheet_rect_for_slot(self, slot):
        scale = self._preview_state["scale"]
        origin_x = self._preview_state["origin_x"]
        origin_y = self._preview_state["origin_y"]
        left = origin_x + ((slot["center"].X - self._bounds_min.X) * scale) - ((slot["width"] * scale) / 2.0)
        top = origin_y + ((self._bounds_max.Y - slot["center"].Y) * scale) - ((slot["height"] * scale) / 2.0)
        box_width = max(slot["width"] * scale, 30.0)
        box_height = max(slot["height"] * scale, 22.0)
        return left, top, box_width, box_height

    def _on_preview_size_changed(self, sender, event_args):
        self._render_preview()

    def _on_source_sheet_changed(self, sender, event_args):
        if self._loading:
            return
        self._load_source_sheet(reset_names=True, reset_sheet_identity=True)

    def _on_duplicate_option_changed(self, sender, event_args):
        if self._loading:
            return
        self._load_source_sheet(reset_names=True, reset_sheet_identity=False)

    def _on_target_name_changed(self, sender, event_args):
        slot = getattr(sender, "Tag", None)
        if slot is None or not slot["can_duplicate"]:
            return
        slot["target_name"] = _normalize_name(sender.Text)
        if slot.get("preview_text") is not None:
            slot["preview_text"].Text = slot["target_name"] or slot["source_name"]

    def _on_reset_names(self, sender, event_args):
        self._reset_target_names()
        for slot in self._viewport_slots:
            if slot.get("text_box") is not None:
                slot["text_box"].Text = slot["target_name"]
        self._render_preview()

    def _on_cancel(self, sender, event_args):
        self.result = None
        self.window.DialogResult = False
        self.window.Close()

    def _on_create(self, sender, event_args):
        source_sheet = self._selected_sheet()
        if source_sheet is None:
            self._txt_warning.Text = "Select a source sheet."
            return

        sheet_number = _normalize_name(self._txt_sheet_number.Text)
        if not sheet_number:
            self._txt_warning.Text = "Enter a new sheet number."
            return

        existing_numbers = _collect_existing_sheet_numbers()
        if sheet_number.upper() in existing_numbers:
            self._txt_warning.Text = "Sheet number already exists."
            return

        duplicate_slots = []
        for slot in self._viewport_slots:
            if slot["can_duplicate"]:
                target_name = _normalize_name(slot["target_name"])
                if not target_name:
                    self._txt_warning.Text = "Every duplicated viewport needs a target view name."
                    return
            else:
                target_name = slot["source_name"]
            duplicate_slots.append(
                {
                    "source_view": slot["source_view"],
                    "source_name": slot["source_name"],
                    "center": DB.XYZ(slot["center"].X, slot["center"].Y, 0.0),
                    "target_name": target_name,
                    "can_duplicate": slot["can_duplicate"],
                }
            )

        self.result = {
            "source_sheet": source_sheet,
            "titleblock_type_id": _get_titleblock_type_id(source_sheet),
            "sheet_number": sheet_number,
            "sheet_name": _normalize_name(self._txt_sheet_name.Text) or _default_sheet_name(source_sheet),
            "duplicate_option": self._selected_option_key(),
            "view_prefix": self._txt_view_prefix.Text,
            "view_suffix": self._txt_view_suffix.Text,
            "viewport_slots": duplicate_slots,
            "schedule_slots": list(self._schedule_slots),
        }
        self.window.DialogResult = True
        self.window.Close()


def _duplicate_sheet(payload):
    titleblock_type_id = payload["titleblock_type_id"]
    duplicate_option = _map_duplicate_option(payload["duplicate_option"])
    existing_view_names = _collect_existing_view_names()
    created_viewports = 0
    reused_viewports = 0
    copied_schedules = 0
    errors = []

    transaction = DB.Transaction(doc, WINDOW_TITLE)
    transaction.Start()
    try:
        new_sheet = DB.ViewSheet.Create(doc, titleblock_type_id)
        new_sheet.SheetNumber = payload["sheet_number"]
        try:
            new_sheet.Name = payload["sheet_name"]
        except Exception:
            param = new_sheet.LookupParameter("Sheet Name")
            if param and not param.IsReadOnly:
                param.Set(payload["sheet_name"])

        for slot in payload["viewport_slots"]:
            source_view = slot["source_view"]
            target_view = source_view
            if slot["can_duplicate"]:
                try:
                    new_view_id = source_view.Duplicate(duplicate_option)
                    target_view = doc.GetElement(new_view_id)
                    unique_name = _ensure_unique_name(existing_view_names, slot["target_name"])
                    try:
                        target_view.Name = unique_name
                    except Exception as exc:
                        errors.append("Failed to rename '{}' to '{}': {}".format(slot["source_name"], unique_name, exc))
                    created_viewports += 1
                except Exception as exc:
                    errors.append("Failed to duplicate '{}': {}".format(slot["source_name"], exc))
                    continue
            else:
                reused_viewports += 1

            try:
                if not DB.Viewport.CanAddViewToSheet(doc, new_sheet.Id, target_view.Id):
                    errors.append("View '{}' cannot be placed on the new sheet.".format(_normalize_name(target_view.Name)))
                    continue
            except Exception:
                pass

            try:
                DB.Viewport.Create(doc, new_sheet.Id, target_view.Id, slot["center"])
            except Exception as exc:
                errors.append("Failed to place '{}' on the new sheet: {}".format(_normalize_name(target_view.Name), exc))

        for slot in payload["schedule_slots"]:
            try:
                DB.ScheduleSheetInstance.Create(doc, new_sheet.Id, slot["view"].Id, slot["point"])
                copied_schedules += 1
            except Exception as exc:
                errors.append("Failed to place schedule '{}': {}".format(slot["name"], exc))

        transaction.Commit()
        return new_sheet, created_viewports, reused_viewports, copied_schedules, errors
    except Exception:
        transaction.RollBack()
        raise


def main():
    sheets, current_sheet_id = _collect_source_sheets()
    if not sheets:
        ui.uiUtils_alert("No sheets found.", title=WINDOW_TITLE)
        return

    dialog = SheetDuplicatorDialog(sheets, current_sheet_id)
    if not dialog.ShowDialog() or not dialog.result:
        return

    try:
        new_sheet, created_viewports, reused_viewports, copied_schedules, errors = _duplicate_sheet(dialog.result)
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
        return

    setattr(config, CONFIG_LAST_SOURCE_SHEET_ID, str(_element_id_value(dialog.result["source_sheet"].Id)))
    setattr(config, CONFIG_LAST_DUPLICATE_OPTION, dialog.result["duplicate_option"])
    setattr(config, CONFIG_LAST_VIEW_PREFIX, dialog.result["view_prefix"])
    setattr(config, CONFIG_LAST_VIEW_SUFFIX, dialog.result["view_suffix"])
    save_config()

    message = "Created sheet {} with {} duplicated viewport(s), {} reused viewport(s), and {} copied schedule instance(s).".format(
        new_sheet.SheetNumber,
        created_viewports,
        reused_viewports,
        copied_schedules,
    )
    if errors:
        message += "\n\nIssues:\n" + "\n".join(errors[:10])
        if len(errors) > 10:
            message += "\n...and {} more.".format(len(errors) - 10)
    ui.uiUtils_alert(message, title=WINDOW_TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
