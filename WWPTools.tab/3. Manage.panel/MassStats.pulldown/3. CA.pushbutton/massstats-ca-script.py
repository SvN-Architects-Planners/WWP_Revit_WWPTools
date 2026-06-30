"""Mass Stats CA - calculate GFA, NSA, units, population and jobs for Canadian projects."""

import clr
import os
import traceback

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))


def _load_dll():
    try:
        revit_version = int(str(__revit__.Application.VersionNumber))  # noqa: F821
    except Exception:
        revit_version = 0
    dll_name = (
        "WWPTools.WpfUI.net8.0-windows.dll" if revit_version >= 2025
        else "WWPTools.WpfUI.net48.dll"
    )
    dll_path = os.path.join(lib_path, dll_name)
    if not os.path.isfile(dll_path):
        raise IOError(
            "WWPTools.WpfUI DLL not found in {}. "
            "Build the solution first (Build -> Build Solution).".format(lib_path)
        )
    if hasattr(clr, "AddReferenceToFileAndPath"):
        clr.AddReferenceToFileAndPath(dll_path)
    else:
        clr.AddReference(dll_path)


def main():
    try:
        _load_dll()
    except IOError as e:
        from pyrevit import forms
        forms.alert(str(e), title="Mass Stats CA")
        return

    from WWPTools.WpfUI import MassStatsCALauncher  # type: ignore

    try:
        MassStatsCALauncher.ShowBySelection(__revit__)  # noqa: F821
    except Exception:
        from pyrevit import forms
        forms.alert(traceback.format_exc(), title="Mass Stats CA - Error")


main()
