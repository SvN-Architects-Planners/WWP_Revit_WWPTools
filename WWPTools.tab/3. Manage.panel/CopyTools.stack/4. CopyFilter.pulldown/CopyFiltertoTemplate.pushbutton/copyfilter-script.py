#!python3
# -*- coding: utf-8 -*-

import os
import sys
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")


from System.IO import File
from System.Windows import Visibility
from System.Windows.Controls import ListBoxItem
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader


script_dir = os.path.dirname(__file__)


def _find_lib_path(start_dir):
    current = os.path.abspath(start_dir)
    for _ in range(8):
        candidate = os.path.join(current, "lib")
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(os.path.join(start_dir, "..", "..", "..", "..", "lib"))


lib_path = _find_lib_path(script_dir)
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
import copy_filter_common as cfc
from WWP_versioning import apply_window_title


uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = " ".join(cfc.read_bundle_title(script_dir, "Copy Filters to Template").splitlines()).strip()


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _build_item(content, tag):
    item = ListBoxItem()
    item.Content = content
    item.Tag = tag
    return item


class CopyFiltersToTemplateDialog(object):
    def __init__(self, template_records):
        self.template_records = list(template_records or [])
        self._active_filter_records = []
        self.result = None

        cfc.ensure_wpfui_theme(lib_path)
        xaml_path = os.path.join(script_dir, "CopyFiltertoTemplateWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._source_combo = self.window.FindName("SourceTemplateCombo")
        self._target_combo = self.window.FindName("TargetTemplateCombo")
        self._filter_search = self.window.FindName("FilterSearchBox")
        self._filters_list = self.window.FindName("FiltersList")
        self._btn_select_visible_filters = self.window.FindName("BtnSelectVisibleFilters")
        self._btn_clear_filters = self.window.FindName("BtnClearFilters")
        self._warning_text = self.window.FindName("WarningText")
        self._footer_status = self.window.FindName("FooterStatus")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._btn_copy = self.window.FindName("BtnCopy")

        self._header_title.Text = WINDOW_TITLE
        self._header_subtitle.Text = "Copy selected filter overrides from one template to another template."

        for record in self.template_records:
            self._source_combo.Items.Add(record["display"])
            self._target_combo.Items.Add(record["display"])
        if self._source_combo.Items.Count > 0:
            self._source_combo.SelectedIndex = 0
        if self._target_combo.Items.Count > 1:
            self._target_combo.SelectedIndex = 1
        elif self._target_combo.Items.Count > 0:
            self._target_combo.SelectedIndex = 0

        self._bind_events()
        self._refresh_source_filters()
        self._update_status()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _bind_events(self):
        self._source_combo.SelectionChanged += self._on_source_changed
        self._target_combo.SelectionChanged += self._on_target_changed
        self._filter_search.TextChanged += self._on_filter_search_changed
        self._btn_select_visible_filters.Click += self._on_select_visible_filters
        self._btn_clear_filters.Click += self._on_clear_filters
        self._btn_cancel.Click += self._on_cancel
        self._btn_copy.Click += self._on_copy

    def _selected_source_record(self):
        index_value = int(self._source_combo.SelectedIndex)
        if index_value < 0 or index_value >= len(self.template_records):
            return None
        return self.template_records[index_value]

    def _selected_target_record(self):
        index_value = int(self._target_combo.SelectedIndex)
        if index_value < 0 or index_value >= len(self.template_records):
            return None
        return self.template_records[index_value]

    def _filtered_filter_records(self):
        filter_text = cfc.normalize_text(self._filter_search.Text).lower()
        if not filter_text:
            return list(self._active_filter_records)
        return [record for record in self._active_filter_records if filter_text in record["display"].lower() or filter_text in record["name"].lower()]

    def _selected_filters(self):
        result = []
        for item in list(self._filters_list.SelectedItems):
            record = getattr(item, "Tag", None)
            if record is not None:
                result.append(record)
        return result

    def _refresh_source_filters(self):
        selected_ids = set(record["id"] for record in self._selected_filters())
        self._filters_list.Items.Clear()
        source_record = self._selected_source_record()
        self._active_filter_records = cfc.collect_filter_records(doc, source_record["view"]) if source_record is not None else []
        for record in self._filtered_filter_records():
            item = _build_item(record["display"], record)
            if record["id"] in selected_ids:
                item.IsSelected = True
            self._filters_list.Items.Add(item)
        self._update_status()

    def _set_warning(self, text):
        if text:
            self._warning_text.Text = text
            self._warning_text.Visibility = Visibility.Visible
        else:
            self._warning_text.Text = ""
            self._warning_text.Visibility = Visibility.Collapsed

    def _update_status(self):
        source_record = self._selected_source_record()
        target_record = self._selected_target_record()
        self._footer_status.Text = "{} filter(s) selected. {} -> {}.".format(
            len(self._selected_filters()),
            source_record["name"] if source_record is not None else "No source",
            target_record["name"] if target_record is not None else "No target",
        )

    def _on_source_changed(self, sender, args):
        self._refresh_source_filters()

    def _on_target_changed(self, sender, args):
        self._update_status()

    def _on_filter_search_changed(self, sender, args):
        self._refresh_source_filters()

    def _on_select_visible_filters(self, sender, args):
        for item in list(self._filters_list.Items):
            item.IsSelected = True
        self._update_status()

    def _on_clear_filters(self, sender, args):
        self._filters_list.UnselectAll()
        self._update_status()

    def _on_cancel(self, sender, args):
        self.result = None
        self.window.DialogResult = False
        self.window.Close()

    def _on_copy(self, sender, args):
        source_record = self._selected_source_record()
        target_record = self._selected_target_record()
        selected_filters = self._selected_filters()
        if source_record is None or target_record is None:
            self._set_warning("Select both a source template and a target template.")
            return
        if source_record["id"] == target_record["id"]:
            self._set_warning("Source and target templates must be different.")
            return
        if not selected_filters:
            self._set_warning("Select at least one filter to copy.")
            return
        self.result = {
            "source_view": source_record["view"],
            "target_view": target_record["view"],
            "filter_ids": [record["id"] for record in selected_filters],
        }
        self.window.DialogResult = True
        self.window.Close()


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project before running this tool.", title=WINDOW_TITLE)
        return

    template_records = cfc.template_view_records(doc)
    if len(template_records) < 2:
        ui.uiUtils_alert("At least two filter-capable view templates are required.", title=WINDOW_TITLE)
        return

    dialog = CopyFiltersToTemplateDialog(template_records)
    if not dialog.ShowDialog() or not dialog.result:
        return

    try:
        cfc.copy_filters_to_targets(
            doc,
            dialog.result["source_view"],
            [dialog.result["target_view"]],
            dialog.result["filter_ids"],
            "Copy Filters to Template",
        )
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
        return

    ui.uiUtils_alert(
        "Updated template '{}'.".format(cfc.normalize_text(dialog.result["target_view"].Name)),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    main()
