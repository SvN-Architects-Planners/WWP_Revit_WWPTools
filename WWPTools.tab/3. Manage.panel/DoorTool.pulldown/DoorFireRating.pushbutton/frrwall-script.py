# -*- coding: utf-8 -*-
import os
import sys
import traceback
import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")


from System.IO import File

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
WINDOW_TITLE = "Publish Fire Rating to Doors"


def _set_owner(window):
    helper = WindowInteropHelper(window)
    helper.Owner = uidoc.Application.MainWindowHandle


class FRRWallDialog(object):
    def __init__(self):
        xaml_path = os.path.join(script_dir, "FRRWallWindow.xaml")
        xaml_text = File.ReadAllText(xaml_path)
        self.window = XamlReader.Parse(xaml_text)
        self.window.Title = WINDOW_TITLE
        apply_window_title(self.window, WINDOW_TITLE)
        _set_owner(self.window)

        self._txt_frr = self.window.FindName("TxtFrrParam")
        self._txt_stc = self.window.FindName("TxtStcParam")
        self._btn_run = self.window.FindName("BtnRun")
        self._btn_cancel = self.window.FindName("BtnCancel")
        self._lbl_status = self.window.FindName("LblStatus")

        self._txt_frr.Text = "FRR Walls"
        self._txt_stc.Text = "STC Walls"

        self._btn_run.Click += self._on_run
        self._btn_cancel.Click += self._on_cancel

        self.frr_param = None
        self.stc_param = None
        self.accepted = False

    def _on_run(self, sender, args):
        self.frr_param = (self._txt_frr.Text or "").strip()
        self.stc_param = (self._txt_stc.Text or "").strip()
        if not self.frr_param:
            self._lbl_status.Text = "FRR parameter name is required."
            return
        self.accepted = True
        self.window.DialogResult = True
        self.window.Close()

    def _on_cancel(self, sender, args):
        self.window.DialogResult = False
        self.window.Close()

    def ShowDialog(self):
        return self.window.ShowDialog()


def is_in_group(element):
    return element.GroupId is not None and element.GroupId != DB.ElementId.InvalidElementId


def get_param_value(element, param_name):
    param = element.LookupParameter(param_name)
    if param is None:
        return None
    if param.StorageType == DB.StorageType.String:
        return param.AsString()
    elif param.StorageType == DB.StorageType.Double:
        return str(param.AsDouble())
    elif param.StorageType == DB.StorageType.Integer:
        return str(param.AsInteger())
    return None


def set_param_value(element, param_name, value):
    param = element.LookupParameter(param_name)
    if param is None or param.IsReadOnly:
        return False
    if param.StorageType == DB.StorageType.String:
        param.Set(value or "")
        return True
    return False


def copy_frr_to_doors(frr_param_name, stc_param_name):
    doors = [
        e for e in DB.FilteredElementCollector(doc)
            .OfCategory(DB.BuiltInCategory.OST_Doors)
            .WhereElementIsNotElementType()
            .ToElements()
        if not is_in_group(e)
    ]

    updated = 0
    skipped_no_host = 0
    skipped_no_param = 0

    t = DB.Transaction(doc, "Publish Fire Rating to Doors")
    t.Start()
    try:
        for door in doors:
            host = door.Host
            if host is None:
                skipped_no_host += 1
                continue

            frr_val = get_param_value(host, frr_param_name)
            if frr_val is None:
                skipped_no_param += 1
            else:
                set_param_value(door, frr_param_name, frr_val)

            if stc_param_name:
                stc_val = get_param_value(host, stc_param_name)
                if stc_val is not None:
                    set_param_value(door, stc_param_name, stc_val)

            updated += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    return updated, skipped_no_host, skipped_no_param


def main():
    if doc is None:
        ui.uiUtils_alert("Open a project first.", title=WINDOW_TITLE)
        return

    dialog = FRRWallDialog()
    if not dialog.ShowDialog():
        return

    updated, skipped_no_host, skipped_no_param = copy_frr_to_doors(
        dialog.frr_param, dialog.stc_param
    )

    lines = ["Done. Updated {} door(s).".format(updated)]
    if skipped_no_host:
        lines.append("{} door(s) skipped — no host wall.".format(skipped_no_host))
    if skipped_no_param:
        lines.append(
            "{} door(s) — host wall missing '{}' parameter.".format(
                skipped_no_param, dialog.frr_param
            )
        )
    ui.uiUtils_alert("\n".join(lines), title=WINDOW_TITLE)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title=WINDOW_TITLE)
