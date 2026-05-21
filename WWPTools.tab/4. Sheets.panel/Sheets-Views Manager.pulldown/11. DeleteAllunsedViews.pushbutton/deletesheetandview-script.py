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
from System.Windows import RoutedEventHandler
from System.Windows.Controls import ListBoxItem, SelectionChangedEventHandler
from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_versioning import apply_window_title
from pyrevit import DB
from System.Collections.Generic import List

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Delete Sheets by Print Set"
PRINT_SET_PARAM = "Print Set"


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


def collect_sheets_by_print_set():
    """Returns a dict: print_set_name -> list of ViewSheet."""
    result = {}
    for sheet in (DB.FilteredElementCollector(doc)
                  .OfClass(DB.ViewSheet)
                  .ToElements()):
        param = sheet.LookupParameter(PRINT_SET_PARAM)
        if param and param.StorageType == DB.StorageType.String:
            ps = (param.AsString() or "").strip() or "???"
        else:
            ps = "???"
        result.setdefault(ps, []).append(sheet)
    return result


class DeleteSheetDialog(object):
    def __init__(self, sheets_by_ps):
        xaml_path = os.path.join(script_dir, "DeleteSheetWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._lst_ps = self.window.FindName("LstPrintSets")
        self._lst_sheets = self.window.FindName("LstSheets")
        self._btn_delete = self.window.FindName("BtnDelete")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")
        self._lbl_count = self.window.FindName("LblCount")

        self._sheets_by_ps = sheets_by_ps
        for ps_name in sorted(sheets_by_ps.keys()):
            item = ListBoxItem()
            item.Content = "{} ({} sheet(s))".format(ps_name, len(sheets_by_ps[ps_name]))
            item.Tag = ps_name
            self._lst_ps.Items.Add(item)

        self._lst_ps.SelectionChanged += SelectionChangedEventHandler(self._on_ps_selected)
        self._btn_delete.Click += RoutedEventHandler(self._on_delete)
        self._btn_cancel.Click += RoutedEventHandler(self._on_cancel)

        self.selected_ps = None
        self.accepted = False

    def _on_ps_selected(self, sender, args):
        sel = self._lst_ps.SelectedItem
        self._lst_sheets.Items.Clear()
        if sel is None:
            self._lbl_count.Text = ""
            return
        ps_name = sel.Tag
        sheets = self._sheets_by_ps.get(ps_name, [])
        self._lbl_count.Text = "{} sheet(s) will be deleted.".format(len(sheets))
        for sheet in sorted(sheets, key=lambda s: s.SheetNumber or ""):
            item = ListBoxItem()
            item.Content = "{} - {}".format(sheet.SheetNumber, sheet.Name)
            self._lst_sheets.Items.Add(item)

    def _on_delete(self, sender, args):
        sel = self._lst_ps.SelectedItem
        if sel is None:
            self._lbl_status.Text = "Select a print set."
            return
        self.selected_ps = sel.Tag
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    sheets_by_ps = collect_sheets_by_print_set()
    if not sheets_by_ps:
        ui.uiUtils_alert("No sheets found in the project.", title=WINDOW_TITLE)
        return

    dialog = DeleteSheetDialog(sheets_by_ps)
    if not dialog.ShowDialog():
        return

    sheets_to_delete = sheets_by_ps.get(dialog.selected_ps, [])
    if not sheets_to_delete:
        return

    confirm_msg = "Permanently delete {} sheet(s) in print set '{}'?\nThis cannot be undone.".format(
        len(sheets_to_delete), dialog.selected_ps
    )
    if not ui.uiUtils_confirm(confirm_msg, title=WINDOW_TITLE):
        return

    t = DB.Transaction(doc, "Delete Sheets by Print Set")
    t.Start()
    try:
        id_col = List[DB.ElementId]()
        for sheet in sheets_to_delete:
            id_col.Add(sheet.Id)
        deleted = doc.Delete(id_col)
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    ui.uiUtils_alert(
        "Deleted {} sheet(s) from print set '{}'.".format(
            len(deleted) if deleted else 0, dialog.selected_ps
        ),
        title=WINDOW_TITLE,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
