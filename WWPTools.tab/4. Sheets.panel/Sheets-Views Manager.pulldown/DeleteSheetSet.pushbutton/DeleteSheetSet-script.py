#!python3
# -*- coding: utf-8 -*-
import os
import sys

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System.IO import File
from System.Windows import MessageBox, MessageBoxButton, MessageBoxImage, RoutedEventHandler, Visibility
from System.Windows.Controls import CheckBox, ListBoxItem, SelectionChangedEventHandler, TextChangedEventHandler
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

from pyrevit import DB, revit

WINDOW_TITLE = "Delete Sheet Set"
EMPTY_VALUE = "???"

uidoc = revit.uidoc
doc = revit.doc


# ---------------------------------------------------------------------------
# Revit data helpers
# ---------------------------------------------------------------------------

def _elem_id_value(element_id):
    try:
        return int(element_id.Value)      # Revit 2024+
    except AttributeError:
        return int(element_id.Value)  # Revit 2023-


def _safe_name(element):
    try:
        return element.Name or ""
    except Exception:
        return ""


def _normalize(text):
    t = (text or "").strip()
    return t if t else EMPTY_VALUE


def _param_value(element, name):
    """Return a display string for a named parameter on an element."""
    if name == "Sheet Number" and hasattr(element, "SheetNumber"):
        return _normalize(element.SheetNumber)
    if name == "Sheet Name":
        return _normalize(_safe_name(element))
    if name == "Is Placeholder" and hasattr(element, "IsPlaceholder"):
        return "Yes" if element.IsPlaceholder else "No"

    param = element.LookupParameter(name)
    if param is None:
        return EMPTY_VALUE
    try:
        v = param.AsValueString()
        if v and v.strip():
            return _normalize(v)
    except Exception:
        pass
    try:
        st = param.StorageType
        if st == DB.StorageType.String:
            return _normalize(param.AsString())
        if st == DB.StorageType.Integer:
            return str(param.AsInteger())
        if st == DB.StorageType.Double:
            return str(param.AsDouble())
        if st == DB.StorageType.ElementId:
            eid = param.AsElementId()
            if eid == DB.ElementId.InvalidElementId:
                return EMPTY_VALUE
            ref = element.Document.GetElement(eid)
            return _normalize(_safe_name(ref)) if ref else EMPTY_VALUE
    except Exception:
        pass
    return EMPTY_VALUE


def collect_sheets(document):
    """Return list of dicts with sheet metadata."""
    sheets = (DB.FilteredElementCollector(document)
              .OfClass(DB.ViewSheet)
              .ToElements())

    # Build parameter name set
    param_names = {"Sheet Number", "Sheet Name", "Is Placeholder"}
    for sheet in sheets:
        for p in sheet.Parameters:
            try:
                n = p.Definition.Name
                if n:
                    param_names.add(n)
            except Exception:
                pass
    param_names = sorted(param_names)

    result = []
    for sheet in sorted(sheets,
                        key=lambda s: (s.SheetNumber or "", _safe_name(s))):
        item = {
            "id": _elem_id_value(sheet.Id),
            "label": "{} - {}".format(sheet.SheetNumber or "", _safe_name(sheet)),
            "selected": False,
            "params": {n: _param_value(sheet, n) for n in param_names},
        }
        result.append(item)

    return result, param_names


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class DeleteSheetSetDialog(object):

    def __init__(self):
        xaml_path = os.path.join(script_dir, "DeleteSheetSetWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE

        helper = WindowInteropHelper(self.window)
        helper.Owner = uidoc.Application.MainWindowHandle

        # Named controls
        self._cmb_param = self.window.FindName("FilterParamCombo")
        self._lst_values = self.window.FindName("FilterValueList")
        self._txt_search = self.window.FindName("SearchBox")
        self._lst_available = self.window.FindName("AvailableList")
        self._lst_preview = self.window.FindName("PreviewList")
        self._lbl_count = self.window.FindName("CountLabel")
        self._txt_validation = self.window.FindName("ValidationText")
        self._btn_apply = self.window.FindName("ApplyFilterButton")
        self._btn_clear = self.window.FindName("ClearFilterButton")
        self._btn_select_all = self.window.FindName("SelectAllButton")
        self._btn_none = self.window.FindName("SelectNoneButton")
        self._btn_invert = self.window.FindName("InvertButton")
        self._btn_refresh = self.window.FindName("RefreshButton")
        self._btn_delete = self.window.FindName("DeleteButton")
        self._btn_close = self.window.FindName("CloseButton")

        # Data
        self._all_items = []
        self._visible_items = []
        self._param_names = []
        self._loading = False

        # Wire up events
        self._btn_apply.Click += RoutedEventHandler(self._on_apply)
        self._btn_clear.Click += RoutedEventHandler(self._on_clear)
        self._btn_select_all.Click += RoutedEventHandler(self._on_select_all)
        self._btn_none.Click += RoutedEventHandler(self._on_none)
        self._btn_invert.Click += RoutedEventHandler(self._on_invert)
        self._btn_refresh.Click += RoutedEventHandler(self._on_refresh)
        self._btn_delete.Click += RoutedEventHandler(self._on_delete)
        self._btn_close.Click += RoutedEventHandler(self._on_close)
        self._cmb_param.SelectionChanged += SelectionChangedEventHandler(self._on_param_changed)
        self._txt_search.TextChanged += TextChangedEventHandler(self._on_search_changed)

    # ------------------------------------------------------------------
    # Load / refresh
    # ------------------------------------------------------------------

    def load(self):
        self._all_items, self._param_names = collect_sheets(doc)
        self._loading = True
        self._cmb_param.Items.Clear()
        for n in self._param_names:
            self._cmb_param.Items.Add(n)
        if self._cmb_param.Items.Count > 0:
            self._cmb_param.SelectedIndex = 0
        self._loading = False
        self._rebuild_value_list()
        self._apply_filters()

    def _rebuild_value_list(self):
        param = self._cmb_param.SelectedItem
        self._lst_values.Items.Clear()
        if not param:
            return
        seen = set()
        for item in self._all_items:
            v = item["params"].get(param, EMPTY_VALUE)
            if v not in seen:
                seen.add(v)
                self._lst_values.Items.Add(v)
        # select all by default
        for i in range(self._lst_values.Items.Count):
            self._lst_values.SelectedItems.Add(self._lst_values.Items[i])

    def _apply_filters(self):
        param = self._cmb_param.SelectedItem or ""
        selected_values = set(
            str(v) for v in self._lst_values.SelectedItems
        )
        search = (self._txt_search.Text or "").strip().lower()

        self._visible_items = []
        for item in self._all_items:
            if param and selected_values:
                if item["params"].get(param, EMPTY_VALUE) not in selected_values:
                    continue
            if search and search not in item["label"].lower():
                continue
            self._visible_items.append(item)

        self._rebuild_available_list()
        self._rebuild_preview_list()
        self._update_count()

    def _rebuild_available_list(self):
        self._lst_available.Items.Clear()
        for item in self._visible_items:
            chk = CheckBox()
            chk.Content = item["label"]
            chk.IsChecked = bool(item["selected"])
            chk.Tag = item
            chk.Checked += RoutedEventHandler(self._on_item_checked)
            chk.Unchecked += RoutedEventHandler(self._on_item_checked)
            lbi = ListBoxItem()
            lbi.Content = chk
            self._lst_available.Items.Add(lbi)

    def _rebuild_preview_list(self):
        self._lst_preview.Items.Clear()
        for item in self._all_items:
            if item["selected"]:
                self._lst_preview.Items.Add(item["label"])

    def _update_count(self):
        selected = sum(1 for i in self._all_items if i["selected"])
        self._lbl_count.Text = "{} selected | {} visible | {} total".format(
            selected, len(self._visible_items), len(self._all_items))

    def _show_validation(self, msg):
        if msg:
            self._txt_validation.Text = msg
            self._txt_validation.Visibility = Visibility.Visible
        else:
            self._txt_validation.Text = ""
            self._txt_validation.Visibility = Visibility.Collapsed

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_param_changed(self, sender, args):
        if self._loading:
            return
        self._rebuild_value_list()
        self._apply_filters()

    def _on_search_changed(self, sender, args):
        self._apply_filters()

    def _on_apply(self, sender, args):
        self._apply_filters()

    def _on_clear(self, sender, args):
        self._txt_search.Text = ""
        if self._cmb_param.Items.Count > 0:
            self._cmb_param.SelectedIndex = 0
        self._rebuild_value_list()
        self._apply_filters()

    def _on_select_all(self, sender, args):
        for item in self._visible_items:
            item["selected"] = True
        self._apply_filters()

    def _on_none(self, sender, args):
        for item in self._visible_items:
            item["selected"] = False
        self._apply_filters()

    def _on_invert(self, sender, args):
        for item in self._visible_items:
            item["selected"] = not item["selected"]
        self._apply_filters()

    def _on_item_checked(self, sender, args):
        chk = sender
        item = chk.Tag
        item["selected"] = bool(chk.IsChecked)
        self._rebuild_preview_list()
        self._update_count()

    def _on_refresh(self, sender, args):
        self.load()

    def _on_delete(self, sender, args):
        to_delete = [i for i in self._all_items if i["selected"]]
        if not to_delete:
            self._show_validation("Select at least one sheet to delete.")
            return
        self._show_validation("")

        confirm = MessageBox.Show(
            self.window,
            "Delete {} sheet(s)? This cannot be undone.".format(len(to_delete)),
            WINDOW_TITLE,
            MessageBoxButton.OKCancel,
            MessageBoxImage.Warning,
        )
        if str(confirm) != "OK":
            return

        deleted = 0
        failed = []
        try:
            from System import Int64
            with DB.Transaction(doc, WINDOW_TITLE) as t:
                t.Start()
                for item in to_delete:
                    try:
                        doc.Delete(DB.ElementId(Int64(item["id"])))
                        deleted += 1
                    except Exception as ex:
                        failed.append("{}: {}".format(item["label"], ex))
                t.Commit()
        except Exception as ex:
            MessageBox.Show(self.window,
                            "Transaction failed:\n{}".format(ex),
                            WINDOW_TITLE,
                            MessageBoxButton.OK,
                            MessageBoxImage.Error)
            return

        msg = "Deleted {} sheet(s).".format(deleted)
        if failed:
            msg += "\n\nFailed ({}):\n{}".format(
                len(failed), "\n".join(failed[:10]))
        MessageBox.Show(self.window, msg, WINDOW_TITLE,
                        MessageBoxButton.OK, MessageBoxImage.Information)
        self.load()

    def _on_close(self, sender, args):
        self.window.Close()

    def show(self):
        self.window.ShowDialog()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    dialog = DeleteSheetSetDialog()
    dialog.load()
    dialog.show()


if __name__ == "__main__":
    main()
