#!python3
# -*- coding: utf-8 -*-

import os
import sys
import traceback

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from Autodesk.Revit import DB

from System.IO import File
from System.Windows import Visibility
from System.Windows.Controls import ListBoxItem, SelectionChangedEventHandler, TextChangedEventHandler
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
WINDOW_TITLE = " ".join(cfc.read_bundle_title(script_dir, "Copy Filters to Current View").splitlines()).strip()


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


class CopyFiltersToCurrentViewDialog(object):
    def __init__(self, current_view, template_records):
        self.current_view = current_view
        self.template_records = list(template_records or [])
        self._active_filter_records = []
        self.result = None

        cfc.ensure_wpfui_theme(lib_path)
        xaml_path = os.path.join(script_dir, "CopyFiltertoCurrentViewWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._source_combo = self.window.FindName("SourceTemplateCombo")
        self._current_view_text = self.window.FindName("CurrentViewText")
        self._filter_search = self.window.FindName("FilterSearchBox")
        self._filters_list = self.window.FindName("FiltersList")
        self._btn_select_visible_filters = self.window.FindName("BtnSelectVisibleFilters")
        self._btn_clear_filters = self.window.FindName("BtnClearFilters")
        self._remove_template_check = self.window.FindName("RemoveTemplateCheck")
        self._warning_text = self.window.FindName("WarningText")
        self._footer_status = self.window.FindName("FooterStatus")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._btn_copy = self.window.FindName("BtnCopy")

        self._header_title.Text = WINDOW_TITLE
        self._header_subtitle.Text = "Copy selected filter overrides from a source template to the active view."
        self._current_view_text.Text = cfc.view_display_name(self.current_view)

        for record in self.template_records:
            self._source_combo.Items.Add(record["display"])
        if self._source_combo.Items.Count > 0:
            self._source_combo.SelectedIndex = 0

        self._remove_template_check.IsChecked = self._current_view_has_template()

        self._bind_events()
        self._refresh_source_filters()
        self._update_status()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _bind_events(self):
        self._source_combo.SelectionChanged += SelectionChangedEventHandler(self._on_source_changed)
        self._filter_search.TextChanged += TextChangedEventHandler(self._on_filter_search_changed)
        self._btn_select_visible_filters.Click += self._on_select_visible_filters
        self._btn_clear_filters.Click += self._on_clear_filters
        self._btn_cancel.Click += self._on_cancel
        self._btn_copy.Click += self._on_copy

    def _current_view_has_template(self):
        try:
            return self.current_view.ViewTemplateId != DB.ElementId.InvalidElementId
        except Exception:
            return False

    def _selected_source_record(self):
        index_value = int(self._source_combo.SelectedIndex)
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
        source_name = source_record["name"] if source_record is not None else "No source template"
        self._footer_status.Text = "{} filter(s) selected from '{}'.".format(len(self._selected_filters()), source_name)

    def _on_source_changed(self, sender, args):
        self._refresh_source_filters()

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
        selected_filters = self._selected_filters()
        remove_template = bool(self._remove_template_check.IsChecked)
        if source_record is None:
            self._set_warning("Select a source template.")
            return
        if not selected_filters:
            self._set_warning("Select at least one filter to copy.")
            return
        if self._current_view_has_template() and not remove_template:
            self._set_warning("The active view still has a view template assigned. Enable template removal before copying filters.")
            return
        self.result = {
            "source_view": source_record["view"],
            "filter_ids": [record["id"] for record in selected_filters],
            "remove_template": remove_template,
        }
        self.window.DialogResult = True
        self.window.Close()


def main():
    if doc is None or uidoc is None:
        ui.uiUtils_alert("Open a project view before running this tool.", title=WINDOW_TITLE)
        return

    current_view = cfc.get_active_view(uidoc)
    if not cfc.is_filterable_view(current_view):
        ui.uiUtils_alert("The active view does not support view filters.", title=WINDOW_TITLE)
        return

    template_records = cfc.template_view_records(doc)
    if not template_records:
        ui.uiUtils_alert("No filter-capable view templates were found in this model.", title=WINDOW_TITLE)
        return

    dialog = CopyFiltersToCurrentViewDialog(current_view, template_records)
    if not dialog.ShowDialog() or not dialog.result:
        return

    try:
        cfc.copy_filters_to_targets(
            doc,
            dialog.result["source_view"],
            [current_view],
            dialog.result["filter_ids"],
            "Copy Filters to Current View",
            clear_target_view_template=dialog.result["remove_template"],
        )
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
        return

    message = "Updated the active view with {} filter(s).".format(len(dialog.result["filter_ids"]))
    if dialog.result["remove_template"]:
        message += "\n\nAny assigned view template was removed before applying the copied overrides."
    ui.uiUtils_alert(message, title=WINDOW_TITLE)


if __name__ == "__main__":
    main()
