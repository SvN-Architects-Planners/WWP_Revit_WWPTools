import os
import sys
import traceback

import clr

from Autodesk.Revit import UI


TITLE = "Delete View Set"
SCRIPT_DIR = os.path.dirname(__file__)
LIB_PATH = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", "..", "..", "lib"))

if LIB_PATH not in sys.path:
    sys.path.append(LIB_PATH)


def _load_wpfui():
    try:
        revit_version = int(str(__revit__.Application.VersionNumber))
    except Exception:
        revit_version = 0

    dll_name = "WWPTools.WpfUI.net8.0-windows.dll" if revit_version >= 2025 else "WWPTools.WpfUI.net48.dll"
    dll_path = os.path.join(LIB_PATH, dll_name)
    if not os.path.isfile(dll_path):
        raise Exception("Missing WPF UI assembly: {}".format(dll_path))

    if hasattr(clr, "AddReferenceToFileAndPath"):
        clr.AddReferenceToFileAndPath(dll_path)
    else:
        clr.AddReference(dll_path)

    from WWPTools.WpfUI import DeleteViewSetLauncher
    return DeleteViewSetLauncher


def main():
    try:
        launcher = _load_wpfui()
        launcher.Show(__revit__)
    except Exception as exc:
        UI.TaskDialog.Show(
            TITLE + " - Error",
            "{}\n\n{}".format(exc, traceback.format_exc()),
        )


if __name__ == "__main__":
    main()
