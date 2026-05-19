# -*- coding: utf-8 -*-
"""
Beta manual revision formatter.

Writes one multiline text parameter that contains repeated
"date + description" blocks across a user-defined number of column sets.
The target titleblock is swapped in only when the formatted content needs
more than one column set, unless disabled by the user.
"""

import os
import sys
import ast
import textwrap
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from pyrevit import revit, DB
from System.IO import File
from System.Windows.Controls import SelectionChangedEventHandler, TextChangedEventHandler
from System.Windows import MessageBox, MessageBoxButton, RoutedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from WWP_settings import get_tool_settings
import WWP_uiUtils as ui
from WWP_versioning import apply_window_title


BLOCK_FIELD = "revision_block"
FIELD_LABELS = {
    BLOCK_FIELD: "Revision block parameter",
}
CONFIG_FIELDS = {
    BLOCK_FIELD: "param_revision_block",
    "date_width": "date_width",
    "desc_width": "desc_width",
    "column_count": "column_count",
    "target_titleblock": "target_titleblock_id",
    "only_override_needed": "only_override_needed",
}
DEFAULT_PARAM_MAP = {
    BLOCK_FIELD: "",
}
DEFAULT_DATE_WIDTH = 23
DEFAULT_DESC_WIDTH = 57
DEFAULT_COLUMN_COUNT = 2
LINE_LIMIT_PER_SET = 10
DATE_MM_PER_CHAR = 2.3
DESC_MM_PER_CHAR = 57.0 / 34.0
NBSP = u"\u00A0"

SCOPE_SHEET = "sheet"
SCOPE_TITLEBLOCK = "titleblock"
FilteredElementCollector = DB.FilteredElementCollector

doc = revit.doc
uidoc = revit.uidoc
config, save_config = get_tool_settings("ManualRevisionsBeta", doc=doc)

_WPFUI_THEME_READY = False


def _read_bundle_title():
    bundle_path = os.path.join(script_dir, "bundle.yaml")
    if not os.path.isfile(bundle_path):
        return "Manual Revisions Beta"
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
    return "Manual Revisions Beta"


BUNDLE_TITLE = _read_bundle_title()
WINDOW_TITLE = " ".join(BUNDLE_TITLE.splitlines()).strip() or "Manual Revisions Beta"


def ensure_wpfui_theme():
    global _WPFUI_THEME_READY
    if _WPFUI_THEME_READY:
        return
    try:
        ui._ensure_theme()
        _WPFUI_THEME_READY = True
    except Exception:
        pass


def _elem_id_int(eid):
    if eid is None:
        return None
    try:
        return int(eid.Value)
    except Exception:
        pass
    try:
        return int(eid.IntegerValue)
    except Exception:
        pass
    try:
        return int(str(eid))
    except Exception:
        return None


def _elem_id_text(eid):
    value = _elem_id_int(eid)
    return "" if value is None else str(value)


def make_param_token(scope, param_name):
    if not param_name:
        return ""
    return "{}|{}".format(scope, param_name)


def parse_param_token(token):
    value = str(token or "").strip()
    if not value:
        return "", ""
    if "|" in value:
        scope, param_name = value.split("|", 1)
        return scope.strip().lower(), param_name.strip()
    return SCOPE_SHEET, value


def normalize_param_token(token):
    scope, param_name = parse_param_token(token)
    if not param_name:
        return ""
    return make_param_token(scope, param_name)


def format_param_option(token):
    scope, param_name = parse_param_token(token)
    if not param_name:
        return ""
    scope_label = "Titleblock" if scope == SCOPE_TITLEBLOCK else "Sheet"
    return "{} [{}]".format(param_name, scope_label)


def get_param(element, param_name):
    if element is None or not param_name:
        return None
    try:
        return element.LookupParameter(param_name)
    except Exception:
        return None


def _is_text_parameter(param):
    if not param or not getattr(param, "Definition", None):
        return False
    try:
        if hasattr(param, "StorageType") and param.StorageType == DB.StorageType.String:
            return True
    except Exception:
        pass
    definition = param.Definition
    try:
        if hasattr(definition, "GetDataType") and hasattr(DB, "SpecTypeId"):
            if definition.GetDataType() == getattr(DB.SpecTypeId, "String", None):
                return True
    except Exception:
        pass
    try:
        if hasattr(definition, "ParameterType") and definition.ParameterType == DB.ParameterType.Text:
            return True
    except Exception:
        pass
    return False


def get_sheet_selection_state():
    all_sheets = list(FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    sheet_list = sorted(all_sheets, key=lambda sheet: sheet.SheetNumber)
    if not sheet_list:
        return [], []

    preselected_ids = set()
    active_sheet_id = None
    try:
        selection_ids = uidoc.Selection.GetElementIds()
    except Exception:
        selection_ids = []
    for element_id in selection_ids:
        element = doc.GetElement(element_id)
        if isinstance(element, DB.ViewSheet):
            preselected_ids.add(_elem_id_int(element.Id))

    try:
        active = uidoc.ActiveView
    except Exception:
        active = None
    if isinstance(active, DB.ViewSheet):
        active_sheet_id = _elem_id_int(active.Id)
        if not preselected_ids:
            preselected_ids.add(active_sheet_id)

    if active_sheet_id is not None:
        active_first = []
        others = []
        for sheet in sheet_list:
            if _elem_id_int(sheet.Id) == active_sheet_id:
                active_first.append(sheet)
            else:
                others.append(sheet)
        sheet_list = active_first + others

    preselected_indices = []
    for index, sheet in enumerate(sheet_list):
        if _elem_id_int(sheet.Id) in preselected_ids:
            preselected_indices.append(index)
    return sheet_list, preselected_indices


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


def get_titleblock_instances(sheet):
    try:
        collector = (
            FilteredElementCollector(doc, sheet.Id)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
        )
        return list(collector.ToElements())
    except Exception:
        return []


def get_all_titleblock_instances():
    try:
        collector = (
            FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
            .WhereElementIsNotElementType()
        )
        return list(collector.ToElements())
    except Exception:
        return []


def get_sheet_titleblock_type_id(sheet):
    for titleblock in get_titleblock_instances(sheet):
        try:
            return titleblock.GetTypeId()
        except Exception:
            continue
    return None


def get_saved_target_titleblock_id():
    try:
        value = getattr(config, CONFIG_FIELDS["target_titleblock"])
    except Exception:
        value = ""
    return str(value or "").strip()


def save_target_titleblock_selection(target_type):
    setattr(
        config,
        CONFIG_FIELDS["target_titleblock"],
        _elem_id_text(getattr(target_type, "Id", None)) if target_type is not None else "",
    )
    save_config()


def get_saved_only_override_needed():
    try:
        return bool(getattr(config, CONFIG_FIELDS["only_override_needed"]))
    except Exception:
        return True


def save_only_override_needed(value):
    setattr(config, CONFIG_FIELDS["only_override_needed"], bool(value))
    save_config()


def get_param_from_scope(sheet, token):
    scope, param_name = parse_param_token(token)
    if not param_name:
        return None
    if scope == SCOPE_TITLEBLOCK:
        for titleblock in get_titleblock_instances(sheet):
            param = get_param(titleblock, param_name)
            if param is not None:
                return param
        return None
    return get_param(sheet, param_name)


def get_parameter_options(sheets, preferred_token=None):
    text_options = set()
    for sheet in list(sheets or []):
        try:
            for param in sheet.Parameters:
                if _is_text_parameter(param):
                    name = getattr(param.Definition, "Name", None)
                    if name:
                        text_options.add(make_param_token(SCOPE_SHEET, name))
        except Exception:
            pass

    for titleblock in get_all_titleblock_instances():
        try:
            for param in titleblock.Parameters:
                if _is_text_parameter(param):
                    name = getattr(param.Definition, "Name", None)
                    if name:
                        text_options.add(make_param_token(SCOPE_TITLEBLOCK, name))
        except Exception:
            pass

    preferred_token = normalize_param_token(preferred_token or "")
    if preferred_token:
        text_options.add(preferred_token)

    return sorted(text_options, key=lambda value: format_param_option(value).lower())


def get_saved_param_token():
    try:
        value = getattr(config, CONFIG_FIELDS[BLOCK_FIELD])
    except Exception:
        value = ""
    return normalize_param_token(value or DEFAULT_PARAM_MAP[BLOCK_FIELD])


def save_param_token(token):
    setattr(config, CONFIG_FIELDS[BLOCK_FIELD], token or "")
    save_config()


def get_saved_layout_settings():
    values = {}
    for key, default_value in [
        ("date_width", DEFAULT_DATE_WIDTH),
        ("desc_width", DEFAULT_DESC_WIDTH),
        ("column_count", DEFAULT_COLUMN_COUNT),
    ]:
        try:
            raw_value = getattr(config, CONFIG_FIELDS[key])
            value = int(raw_value)
        except Exception:
            value = default_value
        values[key] = max(1, value)
    return values


def save_layout_settings(layout_settings):
    for key in ["date_width", "desc_width", "column_count"]:
        setattr(config, CONFIG_FIELDS[key], int(layout_settings[key]))
    save_config()


def read_sheet_revisions(sheet):
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
        revision = doc.GetElement(revision_id)
        if revision is not None:
            revisions.append(revision)
    revisions.sort(key=lambda revision: _elem_id_int(revision.Id) or 0)
    return revisions


def _normalize_multiline_text(value):
    text = "" if value is None else str(value)
    return "\r\n".join(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))


def _wrap_text_to_width(text, width_value):
    width_value = max(1.0, float(width_value))
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
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
        current_width = _estimate_text_width(current_line)
        for word in words[1:]:
            candidate = current_line + " " + word
            candidate_width = _estimate_text_width(candidate)
            if candidate_width <= width_value:
                current_line = candidate
                current_width = candidate_width
            else:
                wrapped_lines.append(current_line)
                if _estimate_text_width(word) <= width_value:
                    current_line = word
                    current_width = _estimate_text_width(word)
                else:
                    overflow_lines = _break_word_to_width(word, width_value)
                    wrapped_lines.extend(overflow_lines[:-1])
                    current_line = overflow_lines[-1]
                    current_width = _estimate_text_width(current_line)
        wrapped_lines.append(current_line)
    return wrapped_lines or [""]


def _fit_single_line_text(text, width_value):
    width_value = max(1.0, float(width_value))
    value = str(text or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
    if _estimate_text_width(value) <= width_value:
        return value
    fitted = ""
    for char in value:
        candidate = fitted + char
        if _estimate_text_width(candidate) > width_value:
            break
        fitted = candidate
    return fitted


def _mm_to_char_width(width_mm, mm_per_char):
    try:
        width_mm = float(width_mm)
    except Exception:
        width_mm = float(DEFAULT_DATE_WIDTH)
    return max(1.0, float(width_mm) / float(mm_per_char))


def _char_width_factor(char):
    if char == " ":
        return 0.45
    if char in ".,:;|!iIl1'`":
        return 0.5
    if char in "-_/()[]{}":
        return 0.65
    if char in "MW@#%&QGOCD":
        return 1.3
    if char.isupper():
        return 1.08
    if char.isdigit():
        return 0.95
    return 0.9


def _estimate_text_width(text):
    return sum([_char_width_factor(char) for char in str(text or "")])


def _break_word_to_width(word, max_width):
    parts = []
    current = ""
    for char in str(word or ""):
        candidate = current + char
        if current and _estimate_text_width(candidate) > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current or not parts:
        parts.append(current)
    return parts


def _pad_right(text, width, fill_text):
    value = str(text or "")
    pad_count = max(0, int(width) - len(value))
    if pad_count <= 0:
        return value
    return value + (fill_text * pad_count)


def _revision_date_text(revision):
    try:
        return str(getattr(revision, "RevisionDate", "") or "").strip()
    except Exception:
        return ""


def _revision_desc_text(revision):
    try:
        return str(getattr(revision, "Description", "") or "").strip()
    except Exception:
        return ""


def _format_row_lines(date_text, desc_text, date_width, desc_width):
    date_lines = [_fit_single_line_text(date_text, date_width)]
    desc_lines = _wrap_text_to_width(desc_text, desc_width)
    line_cost = max(len(date_lines), len(desc_lines))
    date_lines += [""] * max(0, line_cost - len(date_lines))
    desc_lines += [""] * max(0, line_cost - len(desc_lines))

    row_lines = []
    for index in range(line_cost):
        date_part = _pad_right(date_lines[index], date_width, NBSP)
        desc_part = _pad_right(desc_lines[index], desc_width, NBSP)
        row_lines.append("{}{}{}".format(date_part, NBSP, desc_part))
    return row_lines, line_cost


def build_revision_block_text(sheet, layout_settings):
    revisions = read_sheet_revisions(sheet)
    date_width = _mm_to_char_width(layout_settings["date_width"], DATE_MM_PER_CHAR)
    desc_width = _mm_to_char_width(layout_settings["desc_width"], DESC_MM_PER_CHAR)
    column_count = int(layout_settings["column_count"])

    columns = [[] for _ in range(column_count)]
    line_counts = [0 for _ in range(column_count)]

    for revision in revisions:
        row_lines, line_cost = _format_row_lines(
            _revision_date_text(revision),
            _revision_desc_text(revision),
            date_width,
            desc_width,
        )
        placed = False
        for index in range(column_count):
            if line_counts[index] + line_cost <= LINE_LIMIT_PER_SET:
                columns[index].append(row_lines)
                line_counts[index] += line_cost
                placed = True
                break
        if not placed:
            columns[-1].append(row_lines)

    used_columns = 0
    normalized_columns = []
    for column_rows in columns:
        flat_lines = []
        for row_lines in column_rows:
            flat_lines.extend(row_lines)
        if flat_lines:
            used_columns += 1
        while len(flat_lines) < LINE_LIMIT_PER_SET:
            flat_lines.append("")
        normalized_columns.append(flat_lines[:LINE_LIMIT_PER_SET])

    combined_lines = []
    active_column_count = max(1, used_columns)
    for line_index in range(LINE_LIMIT_PER_SET):
        line_segments = []
        for col_index in range(active_column_count):
            segment = normalized_columns[col_index][line_index] if col_index < len(normalized_columns) else ""
            segment_width = date_width + 1 + desc_width
            line_segments.append(_pad_right(segment, segment_width, NBSP))
        combined_lines.append("".join(line_segments))

    while combined_lines and not combined_lines[-1].replace(NBSP, "").strip():
        combined_lines.pop()
    return _normalize_multiline_text("\n".join(combined_lines)), used_columns


def _type_id_integer_value(type_id):
    if type_id is None:
        return None
    try:
        return int(_elem_id_int(type_id))
    except Exception:
        return None


def find_titleblock_type_by_id(titleblock_types, type_id_value):
    type_id_text = _elem_id_text(type_id_value)
    if not type_id_text:
        return None
    for titleblock_type in list(titleblock_types or []):
        if _elem_id_text(titleblock_type.Id) == type_id_text:
            return titleblock_type
    return None


def choose_target_titleblock_id(titleblock_types, current_type_id, saved_target_type_id):
    saved_target = find_titleblock_type_by_id(titleblock_types, saved_target_type_id)
    if saved_target is not None:
        return _elem_id_text(saved_target.Id)
    current_id_text = _elem_id_text(current_type_id)
    for titleblock_type in list(titleblock_types or []):
        current_id = _elem_id_text(titleblock_type.Id)
        if current_id and current_id != current_id_text:
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


def build_missing_sheet_message(missing_by_sheet):
    lines = [
        "The selected text parameter is not available on every updated sheet.",
        "",
    ]
    for sheet_label in missing_by_sheet:
        lines.append(sheet_label)
    return "\n".join(lines)


def _requires_existing_param_before_swap(token):
    scope, _param_name = parse_param_token(token)
    return scope != SCOPE_TITLEBLOCK


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


class ManualRevisionBetaDialog(object):
    def __init__(self, sheet_list, preselected_indices, param_options, selected_param, layout_settings, titleblock_types, target_type_id, only_override_needed):
        self.sheet_list = list(sheet_list or [])
        self.preselected_indices = list(preselected_indices or [])
        self.param_options = list(param_options or [])
        self.layout_settings = dict(layout_settings or {})
        self.titleblock_types = list(titleblock_types or [])
        self._combo_tokens = {}
        self._titleblock_lookup = {}
        self.result = None
        self._loading = True

        ensure_wpfui_theme()
        xaml_path = os.path.join(script_dir, "ManualRevisionBetaWindow.xaml")
        self.window = XamlReader.Parse(File.ReadAllText(xaml_path))
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._sheet_label = self.window.FindName("SheetLabel")
        self._cmb_block_param = self.window.FindName("CmbBlockParam")
        self._txt_date_width = self.window.FindName("TxtDateWidth")
        self._txt_desc_width = self.window.FindName("TxtDescWidth")
        self._txt_column_count = self.window.FindName("TxtColumnCount")
        self._cmb_target_titleblock = self.window.FindName("CmbTargetTitleblock")
        self._chk_only_override_needed = self.window.FindName("ChkOnlyOverrideNeeded")
        self._txt_mapping_info = self.window.FindName("TxtMappingInfo")
        self._sheets_list = self.window.FindName("SheetsList")
        self._btn_select_all = self.window.FindName("BtnSelectAll")
        self._btn_clear_selection = self.window.FindName("BtnClearSelection")
        self._btn_apply = self.window.FindName("BtnApply")
        self._btn_cancel = self.window.FindName("BtnCancel")

        self._header_title.Text = WINDOW_TITLE
        self._sheet_label.Text = "One multiline text parameter will receive the fully formatted revision block."

        self._populate_param_combo(selected_param)
        self._populate_titleblock_combo(target_type_id)
        self._populate_sheets_list()

        self._txt_date_width.Text = str(layout_settings["date_width"])
        self._txt_desc_width.Text = str(layout_settings["desc_width"])
        self._txt_column_count.Text = str(layout_settings["column_count"])
        self._chk_only_override_needed.IsChecked = bool(only_override_needed)

        self._cmb_block_param.SelectionChanged += SelectionChangedEventHandler(self._on_mapping_changed)
        self._cmb_target_titleblock.SelectionChanged += SelectionChangedEventHandler(self._on_mapping_changed)
        self._chk_only_override_needed.Checked += RoutedEventHandler(self._on_mapping_changed)
        self._chk_only_override_needed.Unchecked += RoutedEventHandler(self._on_mapping_changed)
        self._txt_date_width.TextChanged += TextChangedEventHandler(self._on_mapping_changed)
        self._txt_desc_width.TextChanged += TextChangedEventHandler(self._on_mapping_changed)
        self._txt_column_count.TextChanged += TextChangedEventHandler(self._on_mapping_changed)
        self._sheets_list.SelectionChanged += SelectionChangedEventHandler(self._on_mapping_changed)
        self._btn_select_all.Click += RoutedEventHandler(self._on_select_all)
        self._btn_clear_selection.Click += RoutedEventHandler(self._on_clear_selection)
        self._btn_apply.Click += RoutedEventHandler(self._on_apply)
        self._btn_cancel.Click += RoutedEventHandler(self._on_cancel)

        self._loading = False
        self._refresh_summary()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _populate_param_combo(self, selected_value):
        self._cmb_block_param.Items.Clear()
        token_lookup = {}
        for option in self.param_options:
            label = format_param_option(option)
            self._cmb_block_param.Items.Add(label)
            token_lookup[label] = option
        self._combo_tokens[self._cmb_block_param.Name] = token_lookup
        selected_label = format_param_option(selected_value)
        if selected_label and selected_label in token_lookup:
            self._cmb_block_param.SelectedItem = selected_label
        elif self._cmb_block_param.Items.Count > 0:
            self._cmb_block_param.SelectedIndex = 0

    def _populate_titleblock_combo(self, selected_type_id):
        self._cmb_target_titleblock.Items.Clear()
        lookup = {}
        for titleblock_type in self.titleblock_types:
            label = titleblock_display_name(titleblock_type)
            self._cmb_target_titleblock.Items.Add(label)
            lookup[label] = titleblock_type
        self._titleblock_lookup = lookup
        selected_id_text = _elem_id_text(selected_type_id)
        selected_label = ""
        for titleblock_type in self.titleblock_types:
            if _elem_id_text(titleblock_type.Id) == selected_id_text:
                selected_label = titleblock_display_name(titleblock_type)
                break
        if selected_label and selected_label in lookup:
            self._cmb_target_titleblock.SelectedItem = selected_label
        elif self._cmb_target_titleblock.Items.Count > 0:
            self._cmb_target_titleblock.SelectedIndex = 0

    def _populate_sheets_list(self):
        self._sheets_list.Items.Clear()
        current_sheet_id = None
        try:
            active_view = uidoc.ActiveView
            if isinstance(active_view, DB.ViewSheet):
                current_sheet_id = _elem_id_int(active_view.Id)
        except Exception:
            pass
        from System.Windows.Controls import ListBoxItem
        for index, sheet in enumerate(self.sheet_list):
            prefix = "[Current Sheet] " if _elem_id_int(sheet.Id) == current_sheet_id else ""
            item = ListBoxItem()
            item.Content = "{}{} - {}".format(prefix, sheet.SheetNumber, sheet.Name)
            item.Tag = index
            self._sheets_list.Items.Add(item)
            if index in self.preselected_indices:
                self._sheets_list.SelectedItems.Add(item)
        if self._sheets_list.SelectedItems.Count == 0 and self._sheets_list.Items.Count > 0:
            self._sheets_list.SelectedIndex = 0

    def _selected_titleblock_type(self):
        label = str(self._cmb_target_titleblock.SelectedItem or "")
        return self._titleblock_lookup.get(label)

    def _selected_param_token(self):
        label = str(self._cmb_block_param.SelectedItem or "")
        return self._combo_tokens.get(self._cmb_block_param.Name, {}).get(label, "")

    def _get_selected_sheet_indices(self):
        indices = []
        for item in self._sheets_list.SelectedItems:
            try:
                indices.append(int(item.Tag))
            except Exception:
                continue
        return sorted(set(indices))

    def _only_override_needed(self):
        try:
            return bool(self._chk_only_override_needed.IsChecked)
        except Exception:
            return True

    def _get_layout_settings(self):
        errors = []
        values = {}
        for key, textbox, label in [
            ("date_width", self._txt_date_width, "Date width"),
            ("desc_width", self._txt_desc_width, "Description width"),
            ("column_count", self._txt_column_count, "Column sets"),
        ]:
            try:
                value = int(str(textbox.Text or "").strip())
                if value <= 0:
                    raise ValueError
                values[key] = value
            except Exception:
                errors.append(label)
        return values, errors

    def _refresh_summary(self):
        if self._loading:
            return
        param_token = self._selected_param_token()
        layout_settings, layout_errors = self._get_layout_settings()
        if not param_token:
            self._txt_mapping_info.Text = "Select the multiline text parameter to receive the formatted block."
            return
        if layout_errors:
            self._txt_mapping_info.Text = "Date width (mm), description width (mm), and column sets must be positive numbers."
            return
        selected_indices = self._get_selected_sheet_indices()
        if not selected_indices:
            self._txt_mapping_info.Text = "Select one or more sheets to update."
            return
        selected_sheets = [self.sheet_list[index] for index in selected_indices if 0 <= index < len(self.sheet_list)]
        override_needed = 0
        for sheet in selected_sheets:
            _block_text, used_columns = build_revision_block_text(sheet, layout_settings)
            if used_columns > 1:
                override_needed += 1
        if self._only_override_needed():
            self._txt_mapping_info.Text = "{} sheet{} selected. {} need the override based on the formatted block width.".format(
                len(selected_sheets),
                "" if len(selected_sheets) == 1 else "s",
                override_needed,
            )
        else:
            self._txt_mapping_info.Text = "{} sheet{} selected. All will use the selected target titleblock and formatted block.".format(
                len(selected_sheets),
                "" if len(selected_sheets) == 1 else "s",
            )

    def _on_mapping_changed(self, sender, args):
        self._refresh_summary()

    def _on_select_all(self, sender, args):
        self._sheets_list.SelectAll()
        self._refresh_summary()

    def _on_clear_selection(self, sender, args):
        self._sheets_list.UnselectAll()
        self._refresh_summary()

    def _on_apply(self, sender, args):
        param_token = self._selected_param_token()
        layout_settings, layout_errors = self._get_layout_settings()
        if not param_token:
            MessageBox.Show(
                "Select the multiline text parameter to receive the formatted block.",
                WINDOW_TITLE,
                MessageBoxButton.OK,
            )
            return
        if layout_errors:
            MessageBox.Show(
                "Date width (mm), description width (mm), and column sets must be positive numbers.",
                WINDOW_TITLE,
                MessageBoxButton.OK,
            )
            return
        selected_indices = self._get_selected_sheet_indices()
        if not selected_indices:
            MessageBox.Show("Select at least one sheet to update.", WINDOW_TITLE, MessageBoxButton.OK)
            return
        target_type = self._selected_titleblock_type()
        if target_type is None:
            MessageBox.Show("Select the target titleblock before applying.", WINDOW_TITLE, MessageBoxButton.OK)
            return
        self.result = {
            "param_token": param_token,
            "layout_settings": layout_settings,
            "selected_indices": selected_indices,
            "target_type_id": _elem_id_text(target_type.Id),
            "only_override_needed": self._only_override_needed(),
        }
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()


def main():
    sheet_list, preselected_indices = get_sheet_selection_state()
    if not sheet_list:
        ui.uiUtils_alert("No sheets found in the project.", title=WINDOW_TITLE)
        return

    reference_index = preselected_indices[0] if preselected_indices else 0
    reference_sheet = sheet_list[reference_index]
    selected_param = get_saved_param_token()
    layout_settings = get_saved_layout_settings()
    titleblock_types = collect_titleblock_types()
    current_type_id = ""
    current_type = get_sheet_titleblock_type_id(reference_sheet)
    if current_type is not None:
        current_type_id = _elem_id_text(current_type)
    target_type_id = choose_target_titleblock_id(titleblock_types, current_type_id, get_saved_target_titleblock_id())
    param_options = get_parameter_options(sheet_list, preferred_token=selected_param)

    dialog = ManualRevisionBetaDialog(
        sheet_list,
        preselected_indices,
        param_options,
        selected_param,
        layout_settings,
        titleblock_types,
        target_type_id,
        get_saved_only_override_needed(),
    )
    ok = dialog.ShowDialog()
    if not ok or dialog.result is None:
        return

    param_token = dialog.result["param_token"]
    layout_settings = dialog.result["layout_settings"]
    target_type = find_titleblock_type_by_id(titleblock_types, dialog.result["target_type_id"])
    only_override_needed = bool(dialog.result["only_override_needed"])
    sheets = [sheet_list[index] for index in dialog.result["selected_indices"] if 0 <= index < len(sheet_list)]
    if target_type is None or not sheets:
        return

    missing_by_sheet = []
    for sheet in sheets:
        if _requires_existing_param_before_swap(param_token) and get_param_from_scope(sheet, param_token) is None:
            missing_by_sheet.append("{} - {}".format(sheet.SheetNumber, sheet.Name))
    if missing_by_sheet:
        ui.uiUtils_alert(build_missing_sheet_message(missing_by_sheet), title=WINDOW_TITLE)
        return

    save_param_token(param_token)
    save_layout_settings(layout_settings)
    save_target_titleblock_selection(target_type)
    save_only_override_needed(only_override_needed)

    processed_sheets = []
    skipped_sheets = []
    swapped_sheet_count = 0
    write_failures = []

    with revit.Transaction("WWP: Manual Revisions Beta"):
        for sheet in sheets:
            block_text, used_columns = build_revision_block_text(sheet, layout_settings)
            if only_override_needed and used_columns <= 1:
                skipped_sheets.append(sheet)
                continue

            if swap_sheet_titleblock_type(sheet, target_type) > 0:
                swapped_sheet_count += 1

            param = get_param_from_scope(sheet, param_token)
            if param is None or param.IsReadOnly:
                write_failures.append("{} - {}".format(sheet.SheetNumber, sheet.Name))
                continue
            try:
                param.Set(block_text)
                processed_sheets.append(sheet)
            except Exception:
                write_failures.append("{} - {}".format(sheet.SheetNumber, sheet.Name))
                continue

    if write_failures:
        ui.uiUtils_alert(
            "The formatted block parameter could not be written on these sheets:\n\n" + "\n".join(write_failures),
            title=WINDOW_TITLE,
        )
        return

    if not processed_sheets:
        ui.uiUtils_alert(
            "No selected sheets were updated. Check the target parameter and width settings.",
            title=WINDOW_TITLE,
        )
        return

    target_name = titleblock_display_name(target_type) or "target titleblock"
    ui.uiUtils_alert(
        "Applied beta formatting to {} sheet{}. {} sheet{} switched to {}. {} sheet{} skipped because they only needed one column set.".format(
            len(processed_sheets),
            "" if len(processed_sheets) == 1 else "s",
            swapped_sheet_count,
            "" if swapped_sheet_count == 1 else "s",
            target_name,
            len(skipped_sheets),
            "" if len(skipped_sheets) == 1 else "s",
        ),
        title=WINDOW_TITLE,
    )


main()
