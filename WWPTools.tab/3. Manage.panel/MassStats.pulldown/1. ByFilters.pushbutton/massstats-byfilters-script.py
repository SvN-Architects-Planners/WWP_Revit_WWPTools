# -*- coding: utf-8 -*-
#! python3
"""Mass Stats By Filters - live back-of-house calculations from Revit mass elements."""

import clr
import os
import traceback

# ── locate the compiled WPF DLL ─────────────────────────────────────────────
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
    raise FileNotFoundError(
        "WWPTools.WpfUI DLL not found in {}. "
        "Build the solution first (Build → Build Solution).".format(lib_path)
    )

def main():
    try:
        _load_dll()
    except FileNotFoundError as e:
        from pyrevit import forms
        forms.alert(str(e), title="Mass Stats By Filters")
        return

    from WWPTools.WpfUI import MassStatsLauncher  # type: ignore

    try:
        # __revit__ is the UIApplication injected by pyRevit into every script
        MassStatsLauncher.ShowByFilters(__revit__)  # noqa: F821
    except Exception:
        from pyrevit import forms
        forms.alert(traceback.format_exc(), title="Mass Stats By Filters - Error")


main()
