"""Mass Stats By Selection - calculate only the selected Revit mass elements."""

import clr
import os
import traceback

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))


def _load_dll():
    candidates = [
        os.path.join(lib_path, "WWPTools.WpfUI.net48.dll"),
        os.path.join(lib_path, "WWPTools.WpfUI.net8.0-windows.dll"),
        os.path.join(lib_path, "WWPTools.WpfUI.dll"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            clr.AddReference(path)
            return path
    raise IOError(
        "WWPTools.WpfUI DLL not found in {}. "
        "Build the solution first (Build -> Build Solution).".format(lib_path)
    )


def main():
    try:
        _load_dll()
    except IOError as e:
        from pyrevit import forms
        forms.alert(str(e), title="Mass Stats By Selection")
        return

    from WWPTools.WpfUI import MassStatsLauncher  # type: ignore

    try:
        MassStatsLauncher.ShowBySelection(__revit__)  # noqa: F821
    except Exception:
        from pyrevit import forms
        forms.alert(traceback.format_exc(), title="Mass Stats By Selection - Error")


main()
