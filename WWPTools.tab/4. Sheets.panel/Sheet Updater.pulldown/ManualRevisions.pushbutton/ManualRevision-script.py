# -*- coding: utf-8 -*-
"""
WWP Manual Revisions Tool
Toggles between native Revit revision schedule and a manual
4-parameter revision block on the active sheet (or selected sheets).

The sheet parameter mapping is user-selectable and persisted per project.
"""

import os
import sys
import ast
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import revit, DB


from System.IO import File
from System.Windows.Controls import SelectionChangedEventHandler
from System.Windows import MessageBox, MessageBoxButton, MessageBoxResult, RoutedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from WWP_settings import get_tool_settings
import WWP_uiUtils as ui
from WWP_versioning import apply_window_title


FIELD_LEFT_DATES = "left_dates"
FIELD_LEFT_DESCS = "left_descs"
FIELD_RIGHT_DATES = "right_dates"
FIELD_RIGHT_DESCS = "right_descs"

SCOPE_SHEET = "sheet"
SCOPE_TITLEBLOCK = "titleblock"

FIELD_ORDER = [
    FIELD_LEFT_DATES,
    FIELD_LEFT_DESCS,
    FIELD_RIGHT_DATES,
    FIELD_RIGHT_DESCS,
]

FIELD_LABELS = {
    FIELD_LEFT_DATES: "Left dates",
    FIELD_LEFT_DESCS: "Left descriptions",
    FIELD_RIGHT_DATES: "Right dates",
    FIELD_RIGHT_DESCS: "Right descriptions",
}

DEFAULT_PARAM_NAMES = {
    FIELD_LEFT_DATES: "! S_TB_RevisonDate_Left",
    FIELD_LEFT_DESCS: "! S_TB_RevisonDetail_Left",
    FIELD_RIGHT_DATES: "! S_TB_RevisonDate_Right",
    FIELD_RIGHT_DESCS: "! S_TB_RevisonDetail_Right",
}

CONFIG_FIELDS = {
    FIELD_LEFT_DATES: "param_left_dates",
    FIELD_LEFT_DESCS: "param_left_descs",
    FIELD_RIGHT_DATES: "param_right_dates",
    FIELD_RIGHT_DESCS: "param_right_descs",
    "target_titleblock": "target_titleblock_id",
}

doc = revit.doc
uidoc = revit.uidoc
config, save_config = get_tool_settings("ManualRevisions", doc=doc)


_WPFUI_THEME_READY = False


def _read_bundle_title():
    bundle_path = os.path.join(script_dir, "bundle.yaml")
    if not os.path.isfile(bundle_path):
        return "Manual Revisions"
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
    return "Manual Revisions"


BUNDLE_TITLE = _read_bundle_title()
WINDOW_TITLE = " ".join(BUNDLE_TITLE.splitlines()).strip() or "Manual Revisions"
SCHEDULE_LINE_LIMIT = 10
DOUBLE_LINE_DESCRIPTION_LENGTH = 25


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


def make_param_token(scope, param_name):
    if not param_name:
        return ""
    return "{}|{}".format(scope, param_name)


def parse_param_token(field_name, token):
    value = str(token or "").strip()
    if not value:
        return "", ""
    if "|" in value:
        scope, param_name = value.split("|", 1)
        return scope.strip().lower(), param_name.strip()
    return SCOPE_SHEET, value


def format_param_option(field_name, token):
    scope, param_name = parse_param_token(field_name, token)
    if not param_name:
        return ""
    scope_label = "Titleblock" if scope == SCOPE_TITLEBLOCK else "Sheet"
    return "{} [{}]".format(param_name, scope_label)


def normalize_param_token(field_name, token):
    scope, param_name = parse_param_token(field_name, token)
    if not param_name:
        return ""
    return make_param_token(scope, param_name)


DEFAULT_PARAM_MAP = {
    FIELD_LEFT_DATES: make_param_token(SCOPE_SHEET, DEFAULT_PARAM_NAMES[FIELD_LEFT_DATES]),
    FIELD_LEFT_DESCS: make_param_token(SCOPE_SHEET, DEFAULT_PARAM_NAMES[FIELD_LEFT_DESCS]),
    FIELD_RIGHT_DATES: make_param_token(SCOPE_SHEET, DEFAULT_PARAM_NAMES[FIELD_RIGHT_DATES]),
    FIELD_RIGHT_DESCS: make_param_token(SCOPE_SHEET, DEFAULT_PARAM_NAMES[FIELD_RIGHT_DESCS]),
}


def get_param(element, param_name):
    if element is None or not param_name:
        return None
    try:
        return element.LookupParameter(param_name)
    except Exception:
        return None


def get_sheet_selection_state():
    all_sheets = list(FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheet_list = sorted(all_sheets, key=lambda sheet: sheet.SheetNumber)
    if not sheet_list:
        return [], []

    preselected_ids = set()
    active_sheet_id = None
    selection_ids = uidoc.Selection.GetElementIds()
    for element_id in selection_ids:
        element = doc.GetElement(element_id)
        if isinstance(element, DB.ViewSheet):
            preselected_ids.add(_elem_id_int(element.Id))

    if not preselected_ids:
        active = uidoc.ActiveView
        if isinstance(active, DB.ViewSheet):
            preselected_ids.add(_elem_id_int(active.Id))
            active_sheet_id = _elem_id_int(active.Id)
    else:
        active = uidoc.ActiveView
        if isinstance(active, DB.ViewSheet):
            active_sheet_id = _elem_id_int(active.Id)

    if active_sheet_id is not None:
        active_first = []
        others = []
        for sheet in sheet_list:
            try:
                if _elem_id_int(sheet.Id) == active_sheet_id:
                    active_first.append(sheet)
                else:
                    others.append(sheet)
            except Exception:
                others.append(sheet)
        sheet_list = active_first + others

    preselected_indices = []
    for index, sheet in enumerate(sheet_list):
        try:
            if _elem_id_int(sheet.Id) in preselected_ids:
                preselected_indices.append(index)
        except Exception:
            continue
    return sheet_list, preselected_indices


FilteredElementCollector = DB.FilteredElementCollector


def _is_yes_no_parameter(param):
    if not param or not getattr(param, "Definition", None):
        return False

    definition = param.Definition

    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            data_type = definition.GetDataType()
            yes_no_type = getattr(getattr(DB.SpecTypeId, "Boolean", None), "YesNo", None)
            if yes_no_type is not None and data_type == yes_no_type:
                return True
    except Exception:
        pass

    try:
        if hasattr(definition, "ParameterType") and definition.ParameterType == DB.ParameterType.YesNo:
            return True
    except Exception:
        pass

    return False


def _is_text_parameter(param):
    if not param:
        return False
    try:
        return param.StorageType == DB.StorageType.String
    except Exception:
        return False


def get_titleblock_instances(sheet):
    try:
        return list(
            FilteredElementCollector(doc, sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        return []


def get_sheet_titleblock_instance(sheet):
    titleblocks = get_titleblock_instances(sheet)
    return titleblocks[0] if titleblocks else None


def get_sheet_titleblock_type_id(sheet):
    titleblock = get_sheet_titleblock_instance(sheet)
    if titleblock is None:
        return None
    try:
        return titleblock.GetTypeId()
    except Exception:
        return None


def collect_titleblock_types():
    try:
        titleblocks = list(
            FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsElementType()
            .ToElements()
        )
    except Exception:
        titleblocks = []
    return sorted(
        titleblocks,
        key=lambda titleblock: "{}: {}".format(
            getattr(titleblock, "FamilyName", "") or "",
            getattr(titleblock, "Name", "") or "",
        ).lower(),
    )


def titleblock_display_name(titleblock_type):
    if titleblock_type is None:
        return ""
    family_name = getattr(titleblock_type, "FamilyName", "") or ""
    type_name = getattr(titleblock_type, "Name", "") or ""
    return "{}: {}".format(family_name, type_name).strip(": ")


def get_saved_target_titleblock_id():
    target_id = None
    try:
        target_id = getattr(config, CONFIG_FIELDS["target_titleblock"])
    except Exception:
        pass
    return str(target_id or "").strip()


def save_target_titleblock_selection(target_type):
    setattr(
        config,
        CONFIG_FIELDS["target_titleblock"],
        _elem_id_int(str(target_type.Id)) if target_type is not None else "",
    )
    save_config()


def get_param_from_scope(sheet, field_name, token):
    scope, param_name = parse_param_token(field_name, token)
    if not param_name:
        return None
    if scope == SCOPE_TITLEBLOCK:
        for titleblock in get_titleblock_instances(sheet):
            param = get_param(titleblock, param_name)
            if param is not None:
                return param
        return None
    return get_param(sheet, param_name)


def _add_option_token(option_set, field_name, token):
    normalized = normalize_param_token(field_name, token)
    if normalized:
        option_set.add(normalized)


def get_parameter_options(sheets, preferred_tokens=None):
    text_options = set()
    preferred_tokens = preferred_tokens or {}

    for sheet in list(sheets or []):
        try:
            for param in sheet.Parameters:
                if not param or not getattr(param, "Definition", None):
                    continue
                name = getattr(param.Definition, "Name", None)
                if not name:
                    continue
                if _is_text_parameter(param):
                    text_options.add(make_param_token(SCOPE_SHEET, name))
        except Exception:
            pass

    for field_name in FIELD_ORDER:
        _add_option_token(text_options, field_name, DEFAULT_PARAM_MAP[field_name])
        _add_option_token(text_options, field_name, preferred_tokens.get(field_name, ""))

    return {
        FIELD_LEFT_DATES: sorted(text_options, key=lambda value: format_param_option(FIELD_LEFT_DATES, value).lower()),
        FIELD_LEFT_DESCS: sorted(text_options, key=lambda value: format_param_option(FIELD_LEFT_DESCS, value).lower()),
        FIELD_RIGHT_DATES: sorted(text_options, key=lambda value: format_param_option(FIELD_RIGHT_DATES, value).lower()),
        FIELD_RIGHT_DESCS: sorted(text_options, key=lambda value: format_param_option(FIELD_RIGHT_DESCS, value).lower()),
    }


def get_saved_param_map():
    mapping = {}
    for field_name in FIELD_ORDER:
        config_name = CONFIG_FIELDS[field_name]
        try:
            value = getattr(config, config_name)
        except Exception:
            value = ""
        mapping[field_name] = normalize_param_token(
            field_name, value or DEFAULT_PARAM_MAP[field_name]
        )
    return mapping


def save_param_map(param_map):
    for field_name in FIELD_ORDER:
        setattr(config, CONFIG_FIELDS[field_name], param_map.get(field_name, "") or "")
    save_config()


def get_missing_selected_params(sheet, param_map, include_titleblock=True):
    missing = []
    for field_name in FIELD_ORDER:
        token = normalize_param_token(field_name, param_map.get(field_name, "") or "")
        if not token:
            missing.append(field_name)
            continue
        scope, _param_name = parse_param_token(field_name, token)
        if not include_titleblock and scope == SCOPE_TITLEBLOCK:
            continue
        if get_param_from_scope(sheet, field_name, token) is None:
            missing.append(field_name)
    return missing


def read_sheet_values(sheet, param_map):
    values = {}
    for field_name in FIELD_ORDER:
        param = get_param_from_scope(sheet, field_name, param_map.get(field_name, "") or "")
        if param is None:
            values[field_name] = ""
            continue

        try:
            values[field_name] = param.AsString() or ""
        except Exception:
            values[field_name] = ""
    return values


def _revision_sort_key(revision):
    for attr_name in ("SequenceNumber", "RevisionNumber"):
        try:
            value = getattr(revision, attr_name)
            if value is not None:
                return (0, int(value))
        except Exception:
            pass
    try:
        return (1, _elem_id_int(int(revision.Id)))
    except Exception:
        return (2, 0)


def get_sheet_revisions(sheet):
    revision_ids = []
    try:
        if hasattr(sheet, "GetAllRevisionIds"):
            revision_ids = list(sheet.GetAllRevisionIds() or [])
        elif hasattr(sheet, "GetAdditionalRevisionIds"):
            revision_ids = list(sheet.GetAdditionalRevisionIds() or [])
    except Exception:
        revision_ids = []

    revisions = []
    for revision_id in revision_ids:
        try:
            revision = doc.GetElement(revision_id)
        except Exception:
            revision = None
        if revision is not None:
            revisions.append(revision)
    revisions.sort(key=_revision_sort_key)
    return revisions


def _revision_date_text(revision):
    try:
        return str(getattr(revision, "RevisionDate", "") or "").strip()
    except Exception:
        return ""


def _revision_description_text(revision):
    try:
        return str(getattr(revision, "Description", "") or "").strip()
    except Exception:
        return ""


def _wrap_description_lines(desc_text):
    normalized = str(desc_text or "").replace("\r\n", "\n").replace("\r", "\n")
    wrapped_lines = []
    for raw_line in normalized.split("\n"):
        if raw_line == "":
            wrapped_lines.append("")
            continue
        words = raw_line.split()
        if not words:
            wrapped_lines.append("")
            continue
        current_line = words[0]
        for word in words[1:]:
            candidate = "{} {}".format(current_line, word)
            if len(candidate) <= DOUBLE_LINE_DESCRIPTION_LENGTH:
                current_line = candidate
            else:
                wrapped_lines.append(current_line)
                current_line = word
        wrapped_lines.append(current_line)
    return wrapped_lines or [""]


def _revision_line_cost(revision):
    desc_text = _revision_description_text(revision)
    return len(_wrap_description_lines(desc_text))


def _sheet_revision_line_count(sheet):
    return sum([_revision_line_cost(revision) for revision in get_sheet_revisions(sheet)])


def needs_two_column_manual_layout(sheet):
    return _sheet_revision_line_count(sheet) > SCHEDULE_LINE_LIMIT


def _normalize_multiline_text(value):
    text = "" if value is None else str(value)
    return "\r\n".join(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def _build_date_column(rows):
    lines = []
    for date_text, _desc_text, line_cost in rows:
        lines.append(date_text)
        for _ in range(max(0, line_cost - 1)):
            lines.append("")
    return _normalize_multiline_text("\n".join(lines))


def _build_desc_column(rows):
    lines = []
    for _date_text, desc_text, _line_cost in rows:
        lines.extend(_wrap_description_lines(desc_text))
    return _normalize_multiline_text("\n".join(lines))


def build_revision_preview_values(sheet):
    revisions = get_sheet_revisions(sheet)
    left_rows = []
    right_rows = []
    left_line_count = 0

    for revision in revisions:
        line_cost = _revision_line_cost(revision)
        row = (_revision_date_text(revision), _revision_description_text(revision), line_cost)
        if left_line_count + line_cost <= SCHEDULE_LINE_LIMIT:
            left_rows.append(row)
            left_line_count += line_cost
        else:
            right_rows.append(row)

    return {
        FIELD_LEFT_DATES: _build_date_column(left_rows),
        FIELD_LEFT_DESCS: _build_desc_column(left_rows),
        FIELD_RIGHT_DATES: _build_date_column(right_rows),
        FIELD_RIGHT_DESCS: _build_desc_column(right_rows),
    }


def write_sheet_values(sheet, param_map, values):
    for field_name in FIELD_ORDER:
        param = get_param_from_scope(sheet, field_name, param_map.get(field_name, "") or "")
        if param is None or param.IsReadOnly:
            continue

        value = values.get(field_name, "")
        try:
            param.Set(_normalize_multiline_text(value))
        except Exception:
            continue


def _type_id_integer_value(type_id):
    if type_id is None:
        return None
    try:
        return int(_elem_id_int(type_id))
    except Exception:
        pass
    try:
        return int(type_id)
    except Exception:
        return None


def find_titleblock_type_by_id(titleblock_types, type_id_value):
    type_id_text = str(type_id_value or "").strip()
    if not type_id_text:
        return None
    for titleblock_type in list(titleblock_types or []):
        try:
            if _elem_id_int(str(titleblock_type.Id)) == type_id_text:
                return titleblock_type
        except Exception:
            continue
    return None


def choose_target_titleblock_id(titleblock_types, current_type_id, saved_target_type_id):
    saved_target = find_titleblock_type_by_id(titleblock_types, saved_target_type_id)
    if saved_target is not None:
        return _elem_id_int(str(saved_target.Id))

    current_id_text = str(current_type_id or "").strip()
    for titleblock_type in list(titleblock_types or []):
        try:
            current_id = _elem_id_int(str(titleblock_type.Id))
        except Exception:
            continue
        if current_id != current_id_text:
            return current_id
    return current_id_text


def swap_sheet_titleblock_type(sheet, target_type):
    target_id_value = _type_id_integer_value(getattr(target_type, "Id", None))
    target_id = getattr(target_type, "Id", None)
    if target_id is None or target_id_value is None:
        return 0

    swap_count = 0
    for titleblock in get_titleblock_instances(sheet):
        try:
            if _type_id_integer_value(titleblock.GetTypeId()) == target_id_value:
                continue
            titleblock.ChangeTypeId(target_id)
            swap_count += 1
        except Exception:
            continue
    return swap_count


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


class ManualRevisionDialog(object):
    def __init__(self, sheet_list, preselected_indices, param_options, selected_map, titleblock_types, target_type_id):
        self.sheet_list = list(sheet_list or [])
        self.param_options = param_options
        self.preselected_indices = list(preselected_indices or [])
        self.titleblock_types = list(titleblock_types or [])
        self._combo_tokens = {}
        self._titleblock_lookup = {}
        self.result = None
        self._loading = True

        ensure_wpfui_theme()
        xaml_path = os.path.join(script_dir, "ManualRevisionWindow.xaml")
        if not os.path.isfile(xaml_path):
            raise Exception("Missing dialog XAML: {}".format(xaml_path))
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._sheet_label = self.window.FindName("SheetLabel")
        self._cmb_left_dates = self.window.FindName("CmbLeftDatesParam")
        self._cmb_left_descs = self.window.FindName("CmbLeftDescsParam")
        self._cmb_right_dates = self.window.FindName("CmbRightDatesParam")
        self._cmb_right_descs = self.window.FindName("CmbRightDescsParam")
        self._cmb_target_titleblock = self.window.FindName("CmbTargetTitleblock")
        self._chk_ignore_single_column = self.window.FindName("ChkIgnoreSingleColumn")
        self._txt_mapping_info = self.window.FindName("TxtMappingInfo")
        self._sheets_list = self.window.FindName("SheetsList")
        self._btn_select_all = self.window.FindName("BtnSelectAll")
        self._btn_clear_selection = self.window.FindName("BtnClearSelection")
        self._btn_apply = self.window.FindName("BtnApply")
        self._btn_cancel = self.window.FindName("BtnCancel")

        if self._header_title is not None:
            self._header_title.Text = WINDOW_TITLE

        self._sheet_label.Text = "Select one or more sheets to update. The current sheet is shown first."
        self._populate_sheets_list()

        self._populate_combo(self._cmb_left_dates, param_options.get(FIELD_LEFT_DATES, []), selected_map.get(FIELD_LEFT_DATES, ""))
        self._populate_combo(self._cmb_left_descs, param_options.get(FIELD_LEFT_DESCS, []), selected_map.get(FIELD_LEFT_DESCS, ""))
        self._populate_combo(self._cmb_right_dates, param_options.get(FIELD_RIGHT_DATES, []), selected_map.get(FIELD_RIGHT_DATES, ""))
        self._populate_combo(self._cmb_right_descs, param_options.get(FIELD_RIGHT_DESCS, []), selected_map.get(FIELD_RIGHT_DESCS, ""))
        self._populate_titleblock_combo(self._cmb_target_titleblock, target_type_id)

        for combo_box in [
            self._cmb_left_dates,
            self._cmb_left_descs,
            self._cmb_right_dates,
            self._cmb_right_descs,
        ]:
            combo_box.SelectionChanged += SelectionChangedEventHandler(self._on_mapping_changed)

        self._cmb_target_titleblock.SelectionChanged += SelectionChangedEventHandler(self._on_mapping_changed)
        self._chk_ignore_single_column.Checked += RoutedEventHandler(self._on_mapping_changed)
        self._chk_ignore_single_column.Unchecked += RoutedEventHandler(self._on_mapping_changed)
        self._sheets_list.SelectionChanged += SelectionChangedEventHandler(self._on_selection_changed)
        self._btn_select_all.Click += RoutedEventHandler(self._on_select_all)
        self._btn_clear_selection.Click += RoutedEventHandler(self._on_clear_selection)
        self._btn_apply.Click += RoutedEventHandler(self._on_apply)
        self._btn_cancel.Click += RoutedEventHandler(self._on_cancel)

        self._loading = False
        self._refresh_values_from_mapping()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _populate_sheets_list(self):
        self._sheets_list.Items.Clear()
        current_sheet_id = None
        try:
            active_view = uidoc.ActiveView
            if isinstance(active_view, DB.ViewSheet):
                current_sheet_id = _elem_id_int(active_view.Id)
        except Exception:
            current_sheet_id = None

        for index, sheet in enumerate(self.sheet_list):
            sheet_prefix = ""
            try:
                if current_sheet_id is not None and _elem_id_int(sheet.Id) == current_sheet_id:
                    sheet_prefix = "[Current Sheet] "
            except Exception:
                sheet_prefix = ""
            item = self._make_list_item(
                "{}{} - {}".format(sheet_prefix, sheet.SheetNumber, sheet.Name),
                index,
            )
            self._sheets_list.Items.Add(item)
            if index in self.preselected_indices:
                self._sheets_list.SelectedItems.Add(item)

        if self._sheets_list.SelectedItems.Count == 0 and self._sheets_list.Items.Count > 0:
            self._sheets_list.SelectedIndex = 0

    @staticmethod
    def _make_list_item(label, index):
        from System.Windows.Controls import ListBoxItem


def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

        item = ListBoxItem()
        item.Content = label
        item.Tag = index
        return item

    @staticmethod
    def _type_id_string(titleblock_type):
        try:
            return _elem_id_int(str(titleblock_type.Id))
        except Exception:
            return ""

    def _populate_titleblock_combo(self, combo_box, selected_type_id):
        combo_box.Items.Clear()
        lookup = {}
        for titleblock_type in self.titleblock_types:
            label = titleblock_display_name(titleblock_type)
            combo_box.Items.Add(label)
            lookup[label] = titleblock_type
        self._titleblock_lookup[combo_box.Name] = lookup

        selected_id_text = str(selected_type_id or "").strip()
        selected_label = ""
        for titleblock_type in self.titleblock_types:
            if self._type_id_string(titleblock_type) == selected_id_text:
                selected_label = titleblock_display_name(titleblock_type)
                break
        if selected_label and selected_label in lookup:
            combo_box.SelectedItem = selected_label
        elif combo_box.Items.Count > 0:
            combo_box.SelectedIndex = 0

    def _selected_titleblock_type(self, combo_box):
        try:
            item = combo_box.SelectedItem
            label = str(item) if item is not None else ""
            return self._titleblock_lookup.get(combo_box.Name, {}).get(label)
        except Exception:
            return None

    def _populate_combo(self, combo_box, options, selected_value):
        combo_box.Items.Clear()
        token_lookup = {}
        for option in options:
            label = format_param_option(self._field_name_for_combo(combo_box), option)
            combo_box.Items.Add(label)
            token_lookup[label] = option
        self._combo_tokens[combo_box.Name] = token_lookup

        selected_label = format_param_option(self._field_name_for_combo(combo_box), selected_value)
        if selected_label and selected_label in list(token_lookup.keys()):
            combo_box.SelectedItem = selected_label
        elif combo_box.Items.Count > 0:
            combo_box.SelectedIndex = 0

    def _get_param_map(self):
        return {
            FIELD_LEFT_DATES: self._combo_value(self._cmb_left_dates),
            FIELD_LEFT_DESCS: self._combo_value(self._cmb_left_descs),
            FIELD_RIGHT_DATES: self._combo_value(self._cmb_right_dates),
            FIELD_RIGHT_DESCS: self._combo_value(self._cmb_right_descs),
        }

    def _ignore_single_column(self):
        try:
            return bool(self._chk_ignore_single_column.IsChecked)
        except Exception:
            return False

    def _get_selected_sheet_indices(self):
        indices = []
        try:
            for item in self._sheets_list.SelectedItems:
                indices.append(int(item.Tag))
        except Exception:
            pass
        return sorted(set(indices))

    def _combo_value(self, combo_box):
        try:
            item = combo_box.SelectedItem
            label = str(item) if item is not None else ""
            return self._combo_tokens.get(combo_box.Name, {}).get(label, "")
        except Exception:
            return ""

    @staticmethod
    def _field_name_for_combo(combo_box):
        mapping = {
            "CmbLeftDatesParam": FIELD_LEFT_DATES,
            "CmbLeftDescsParam": FIELD_LEFT_DESCS,
            "CmbRightDatesParam": FIELD_RIGHT_DATES,
            "CmbRightDescsParam": FIELD_RIGHT_DESCS,
        }
        return mapping.get(combo_box.Name, FIELD_LEFT_DATES)

    def _refresh_values_from_mapping(self):
        if self._loading:
            return

        param_map = self._get_param_map()
        missing = [FIELD_LABELS[field_name] for field_name in FIELD_ORDER if not param_map.get(field_name)]
        if missing:
            self._txt_mapping_info.Text = "Select all four parameters before applying."
            return
        selected_indices = self._get_selected_sheet_indices()
        if not selected_indices:
            self._txt_mapping_info.Text = "Select one or more sheets to update."
            return

        selected_sheets = [self.sheet_list[index] for index in selected_indices if 0 <= index < len(self.sheet_list)]
        target_type = self._selected_titleblock_type(self._cmb_target_titleblock)
        target_name = titleblock_display_name(target_type) or "target titleblock"
        ignored_count = len([sheet for sheet in selected_sheets if not needs_two_column_manual_layout(sheet)]) if self._ignore_single_column() else 0
        processed_count = max(0, len(selected_sheets) - ignored_count)
        if self._ignore_single_column():
            self._txt_mapping_info.Text = (
                "{} sheet{} selected. {} will be swapped to {}. {} will be ignored because the revision table does not need two columns."
            ).format(
                len(selected_sheets),
                "" if len(selected_sheets) == 1 else "s",
                processed_count,
                target_name,
                ignored_count,
            )
        else:
            self._txt_mapping_info.Text = (
                "{} sheet{} selected. All selected sheets will be swapped to {} and will use the fake revision parameters."
            ).format(
                len(selected_sheets),
                "" if len(selected_sheets) == 1 else "s",
                target_name,
            )

    def _on_mapping_changed(self, sender, args):
        self._refresh_values_from_mapping()

    def _on_selection_changed(self, sender, args):
        self._refresh_values_from_mapping()

    def _on_select_all(self, sender, args):
        self._sheets_list.SelectAll()
        self._refresh_values_from_mapping()

    def _on_clear_selection(self, sender, args):
        self._sheets_list.UnselectAll()
        self._refresh_values_from_mapping()

    def _on_apply(self, sender, args):
        param_map = self._get_param_map()
        missing_labels = [FIELD_LABELS[field_name] for field_name in FIELD_ORDER if not param_map.get(field_name)]
        if missing_labels:
            MessageBox.Show(
                "Select all four parameters before applying:\n\n" + "\n".join(missing_labels),
                WINDOW_TITLE,
                MessageBoxButton.OK,
            )
            return

        selected_indices = self._get_selected_sheet_indices()
        if not selected_indices:
            MessageBox.Show(
                "Select at least one sheet to update.",
                WINDOW_TITLE,
                MessageBoxButton.OK,
            )
            return

        target_type = self._selected_titleblock_type(self._cmb_target_titleblock)
        if target_type is None:
            MessageBox.Show(
                "Select the target titleblock before applying.",
                WINDOW_TITLE,
                MessageBoxButton.OK,
            )
            return

        self.result = {
            "param_map": param_map,
            "selected_indices": selected_indices,
            "target_type_id": _elem_id_int(str(target_type.Id)),
            "ignore_single_column": self._ignore_single_column(),
        }
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()


def build_missing_sheet_message(missing_by_sheet):
    lines = [
        "The selected parameter mapping is not available on every updated sheet.",
        "",
    ]
    for sheet_label, field_names in missing_by_sheet:
        lines.append("{}: {}".format(sheet_label, ", ".join(field_names)))
    lines.append("")
    lines.append("Adjust the dropdown mapping or update the target titleblock and parameters, then run the tool again.")
    return "\n".join(lines)


def main():
    sheet_list, preselected_indices = get_sheet_selection_state()
    if not sheet_list:
        ui.uiUtils_alert("No sheets found in the project.", title=WINDOW_TITLE)
        return

    reference_index = preselected_indices[0] if preselected_indices else 0
    reference_sheet = sheet_list[reference_index]
    selected_map = get_saved_param_map()
    titleblock_types = collect_titleblock_types()
    current_type_id = ""
    reference_titleblock_type_id = get_sheet_titleblock_type_id(reference_sheet)
    if reference_titleblock_type_id is not None:
        current_type_id = str(_elem_id_int(reference_titleblock_type_id))
    saved_target_type_id = get_saved_target_titleblock_id()
    target_type_id = choose_target_titleblock_id(titleblock_types, current_type_id, saved_target_type_id)

    param_options = get_parameter_options(sheet_list, preferred_tokens=selected_map)
    if not param_options.get(FIELD_LEFT_DATES):
        ui.uiUtils_alert(
            "No text sheet parameters were found on the project sheets.",
            title=WINDOW_TITLE,
        )
        return

    dialog = ManualRevisionDialog(
        sheet_list,
        preselected_indices,
        param_options,
        selected_map,
        titleblock_types,
        target_type_id,
    )
    ok = dialog.ShowDialog()

    if not ok or dialog.result is None:
        return

    param_map = dialog.result["param_map"]
    selected_indices = dialog.result.get("selected_indices", [])
    target_type = find_titleblock_type_by_id(titleblock_types, dialog.result.get("target_type_id", ""))
    ignore_single_column = bool(dialog.result.get("ignore_single_column", False))
    sheets = [sheet_list[index] for index in selected_indices if 0 <= index < len(sheet_list)]
    if not sheets:
        return
    if target_type is None:
        ui.uiUtils_alert(
            "The selected target titleblock could not be resolved.",
            title=WINDOW_TITLE,
        )
        return

    missing_by_sheet = []
    for sheet in sheets:
        missing_fields = get_missing_selected_params(sheet, param_map, include_titleblock=False)
        if missing_fields:
            missing_by_sheet.append((
                "{} — {}".format(sheet.SheetNumber, sheet.Name),
                [format_param_option(field_name, param_map.get(field_name)) or FIELD_LABELS[field_name] for field_name in missing_fields],
            ))

    if missing_by_sheet:
        ui.uiUtils_alert(
            build_missing_sheet_message(missing_by_sheet),
            title=WINDOW_TITLE,
        )
        return

    save_param_map(param_map)
    save_target_titleblock_selection(target_type)

    ignored_sheets = []
    sheets_to_process = []
    for sheet in sheets:
        if ignore_single_column and not needs_two_column_manual_layout(sheet):
            ignored_sheets.append(sheet)
        else:
            sheets_to_process.append(sheet)

    if not sheets_to_process:
        ui.uiUtils_alert(
            "Ignored {} sheet{} because the revision table does not need two columns.".format(
                len(ignored_sheets),
                "" if len(ignored_sheets) == 1 else "s",
            ),
            title=WINDOW_TITLE,
        )
        return

    swapped_sheet_count = 0
    target_name = titleblock_display_name(target_type) or "target titleblock"
    with revit.Transaction("WWP: Manual Revisions"):
        for sheet in sheets_to_process:
            if swap_sheet_titleblock_type(sheet, target_type) > 0:
                swapped_sheet_count += 1
            sheet_values = build_revision_preview_values(sheet)
            write_sheet_values(sheet, param_map, sheet_values)
            missing_fields = get_missing_selected_params(sheet, param_map)
            if missing_fields:
                missing_by_sheet.append((
                    "{} — {}".format(sheet.SheetNumber, sheet.Name),
                    [format_param_option(field_name, param_map.get(field_name)) or FIELD_LABELS[field_name] for field_name in missing_fields],
                ))

    if missing_by_sheet:
        ui.uiUtils_alert(
            build_missing_sheet_message(missing_by_sheet),
            title=WINDOW_TITLE,
        )
        return

    ui.uiUtils_alert(
        "Applied to {} sheet{}. {} sheet{} switched to {}. {} sheet{} ignored because the revision table does not need two columns.".format(
            len(sheets_to_process),
            "s" if len(sheets_to_process) > 1 else "",
            swapped_sheet_count,
            "s" if swapped_sheet_count != 1 else "",
            target_name,
            len(ignored_sheets),
            "" if len(ignored_sheets) == 1 else "s",
        ),
        title=WINDOW_TITLE,
    )


main()
