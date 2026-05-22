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
from System.Windows.Controls import ListBoxItem, TextChangedEventHandler
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
WINDOW_TITLE = " ".join(cfc.read_bundle_title(script_dir, "Copy Current Filters to Multiple Templates").splitlines()).strip()


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


class CopyCurrentFiltersDialog(object):
    def __init__(self, source_view, target_records, filter_records):
        self.source_view = source_view
        self.target_records = list(target_records or [])
        self.filter_records = list(filter_records or [])
        self.result = None

        cfc.ensure_wpfui_theme(lib_path)
        xaml_path = os.path.join(script_dir, "CopyCurrentFiltertMultViewWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._header_title = self.window.FindName("HeaderTitle")
        self._header_subtitle = self.window.FindName("HeaderSubtitle")
        self._source_view_text = self.window.FindName("SourceViewText")
        self._target_search = self.window.FindName("TargetSearchBox")
        self._targets_list = self.window.FindName("TargetsList")
        self._btn_select_visible_targets = self.window.FindName("BtnSelectVisibleTargets")
        self._btn_clear_targets = self.window.FindName("BtnClearTargets")
        self._filter_search = self.window.FindName("FilterSearchBox")
        self._filters_list = self.window.FindName("FiltersList")
        self._btn_select_visible_filters = self.window.FindName("BtnSelectVisibleFilters")
        self._btn_clear_filters = self.window.FindName("BtnClearFilters")
        self._warning_text = self.window.FindName("WarningText")
        self._footer_status = self.window.FindName("FooterStatus")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._btn_copy = self.window.FindName("BtnCopy")

        self._header_title.Text = WINDOW_TITLE
        self._header_subtitle.Text = "Copy selected filter graphic overrides from the active view to one or more view templates."
        self._source_view_text.Text = cfc.view_display_name(self.source_view)

        self._bind_events()
        self._refresh_targets()
        self._refresh_filters()
        self._update_status()

    def ShowDialog(self):
        return self.window.ShowDialog()

    def _bind_events(self):
        self._target_search.TextChanged += TextChangedEventHandler(self._on_target_search_changed)
        self._filter_search.TextChanged += TextChangedEventHandler(self._on_filter_search_changed)
        self._btn_select_visible_targets.Click += self._on_select_visible_targets
        self._btn_clear_targets.Click += self._on_clear_targets
        self._btn_select_visible_filters.Click += self._on_select_visible_filters
        self._btn_clear_filters.Click += self._on_clear_filters
        self._btn_cancel.Click += self._on_cancel
        self._btn_copy.Click += self._on_copy

    def _filtered_target_records(self):
        filter_text = cfc.normalize_text(self._target_search.Text).lower()
        if not filter_text:
            return list(self.target_records)
        return [record for record in self.target_records if filter_text in record["display"].lower() or filter_text in record["name"].lower()]

    def _filtered_filter_records(self):
        filter_text = cfc.normalize_text(self._filter_search.Text).lower()
        if not filter_text:
            return list(self.filter_records)
        return [record for record in self.filter_records if filter_text in record["display"].lower() or filter_text in record["name"].lower()]

    def _refresh_targets(self):
        selected_ids = set(record["id"] for record in self._selected_targets())
        self._targets_list.Items.Clear()
        for record in self._filtered_target_records():
            item = _build_item(record["display"], record)
            if record["id"] in selected_ids:
                item.IsSelected = True
            self._targets_list.Items.Add(item)

    def _refresh_filters(self):
        selected_ids = set(record["id"] for record in self._selected_filters())
        self._filters_list.Items.Clear()
        for record in self._filtered_filter_records():
            item = _build_item(record["display"], record)
            if record["id"] in selected_ids:
                item.IsSelected = True
            self._filters_list.Items.Add(item)

    def _selected_targets(self):
        result = []
        for item in list(self._targets_list.SelectedItems):
            record = getattr(item, "Tag", None)
            if record is not None:
                result.append(record)
        return result

    def _selected_filters(self):
        result = []
        for item in list(self._filters_list.SelectedItems):
            record = getattr(item, "Tag", None)
            if record is not None:
                result.append(record)
        return result

    def _set_warning(self, text):
        if text:
            self._warning_text.Text = text
            self._warning_text.Visibility = Visibility.Visible
        else:
            self._warning_text.Text = ""
            self._warning_text.Visibility = Visibility.Collapsed

    def _update_status(self):
        self._footer_status.Text = "{} template(s) selected, {} filter(s) selected.".format(
            len(self._selected_targets()),
            len(self._selected_filters()),
        )

    def _on_target_search_changed(self, sender, args):
        self._refresh_targets()
        self._update_status()

    def _on_filter_search_changed(self, sender, args):
        self._refresh_filters()
        self._update_status()

    def _on_select_visible_targets(self, sender, args):
        for item in list(self._targets_list.Items):
            item.IsSelected = True
        self._update_status()

    def _on_clear_targets(self, sender, args):
        self._targets_list.UnselectAll()
        self._update_status()

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
        selected_targets = self._selected_targets()
        selected_filters = self._selected_filters()
        if not selected_targets:
            self._set_warning("Select at least one target template.")
            return
        if not selected_filters:
            self._set_warning("Select at least one filter to copy.")
            return
        self.result = {
            "target_views": [record["view"] for record in selected_targets],
            "filter_ids": [record["id"] for record in selected_filters],
        }
        self.window.DialogResult = True
        self.window.Close()


def main():
    if doc is None or uidoc is None:
        ui.uiUtils_alert("Open a project view before running this tool.", title=WINDOW_TITLE)
        return

    source_view = cfc.get_active_view(uidoc)
    if not cfc.is_filterable_view(source_view):
        ui.uiUtils_alert("The active view does not support view filters.", title=WINDOW_TITLE)
        return

    filter_records = cfc.collect_filter_records(doc, source_view)
    if not filter_records:
        ui.uiUtils_alert("The active view has no filters to copy.", title=WINDOW_TITLE)
        return

    target_records = cfc.template_view_records(doc)
    if not target_records:
        ui.uiUtils_alert("No filter-capable view templates were found in this model.", title=WINDOW_TITLE)
        return

    dialog = CopyCurrentFiltersDialog(source_view, target_records, filter_records)
    if not dialog.ShowDialog() or not dialog.result:
        return

    try:
        updated = cfc.copy_filters_to_targets(
            doc,
            source_view,
            dialog.result["target_views"],
            dialog.result["filter_ids"],
            "Copy Current Filters to Multiple Templates",
        )
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
        return

    ui.uiUtils_alert(
        "Updated {} template(s) from the active view.".format(len(updated)),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    main()
