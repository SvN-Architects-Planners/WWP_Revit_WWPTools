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
from System.Windows.Markup import XamlReader
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Controls import ListBoxItem

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_versioning import apply_window_title
from pyrevit import DB

uidoc = getattr(__revit__, "ActiveUIDocument", None)
doc = uidoc.Document if uidoc else None
WINDOW_TITLE = "Door Type Duplicator"


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


def collect_door_types():
    types = []
    for elem in (DB.FilteredElementCollector(doc)
                 .OfCategory(DB.BuiltInCategory.OST_Doors)
                 .WhereElementIsElementType()
                 .ToElements()):
        name = DB.Element.Name.GetValue(elem)
        fam_name = ""
        fam_param = elem.get_Parameter(DB.BuiltInParameter.ELEM_FAMILY_PARAM)
        if fam_param:
            fam_name = fam_param.AsValueString() or ""
        types.append((fam_name, name, elem))
    types.sort(key=lambda x: (x[0], x[1]))
    return types


class DoorTypeDuplicatorDialog(object):
    def __init__(self, door_types):
        xaml_path = os.path.join(script_dir, "DoorTypeDuplicatorWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        ui.uiUtils_ensure_theme()
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._lst = self.window.FindName("LstDoorTypes")
        self._txt_name = self.window.FindName("TxtNewName")
        self._chk_custom = self.window.FindName("ChkCustomSize")
        self._pnl_custom = self.window.FindName("PnlCustomSize")
        self._txt_width = self.window.FindName("TxtWidth")
        self._txt_height = self.window.FindName("TxtHeight")
        self._btn_duplicate = self.window.FindName("BtnDuplicate")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")

        self._door_types = door_types
        for fam, name, elem in door_types:
            item = ListBoxItem()
            item.Content = "{} : {}".format(fam, name) if fam else name
            item.Tag = elem
            self._lst.Items.Add(item)

        self._chk_custom.Checked += self._on_custom_toggle
        self._chk_custom.Unchecked += self._on_custom_toggle
        self._btn_duplicate.Click += self._on_duplicate
        self._btn_cancel.Click += self._on_cancel

        self._pnl_custom.Visibility = Visibility.Collapsed
        self.result_type = None
        self.accepted = False

    def _on_custom_toggle(self, sender, args):
        if self._chk_custom.IsChecked:
            self._pnl_custom.Visibility = Visibility.Visible
        else:
            self._pnl_custom.Visibility = Visibility.Collapsed

    def _on_duplicate(self, sender, args):
        sel_item = self._lst.SelectedItem
        if sel_item is None:
            self._lbl_status.Text = "Select a door type to duplicate."
            return
        new_name = (self._txt_name.Text or "").strip()
        if not new_name:
            self._lbl_status.Text = "Enter a name for the new type."
            return

        source_type = sel_item.Tag
        try:
            new_type = source_type.Duplicate(new_name)
        except Exception as ex:
            self._lbl_status.Text = "Error: " + str(ex)
            return

        if self._chk_custom.IsChecked:
            try:
                w_mm = float(self._txt_width.Text or "0")
                h_mm = float(self._txt_height.Text or "0")
                if w_mm > 0:
                    w_param = new_type.LookupParameter("Width")
                    if w_param and not w_param.IsReadOnly:
                        w_param.Set(DB.UnitUtils.ConvertToInternalUnits(
                            w_mm, DB.UnitTypeId.Millimeters))
                if h_mm > 0:
                    h_param = new_type.LookupParameter("Height")
                    if h_param and not h_param.IsReadOnly:
                        h_param.Set(DB.UnitUtils.ConvertToInternalUnits(
                            h_mm, DB.UnitTypeId.Millimeters))
            except Exception:
                pass

        self.result_type = new_type
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

    door_types = collect_door_types()
    if not door_types:
        ui.uiUtils_alert("No door types found in the project.", title=WINDOW_TITLE)
        return

    dialog = DoorTypeDuplicatorDialog(door_types)

    t = DB.Transaction(doc, "Duplicate Door Type")
    t.Start()
    try:
        result = dialog.ShowDialog()
        if result and dialog.accepted:
            t.Commit()
            ui.uiUtils_alert(
                "Created new door type: '{}'".format(
                    DB.Element.Name.GetValue(dialog.result_type)
                ),
                title=WINDOW_TITLE,
            )
        else:
            t.RollBack()
    except Exception:
        t.RollBack()
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
