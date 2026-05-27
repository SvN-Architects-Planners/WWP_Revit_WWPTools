# -*- coding: utf-8 -*-

import os
import sys
import importlib
from pyrevit import revit
import WWP_uiUtils as ui

script_dir = os.path.dirname(__file__)
pulldown_dir = os.path.abspath(os.path.join(script_dir, ".."))


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
for path in (pulldown_dir, lib_path):
    if path not in sys.path:
        sys.path.append(path)

for module_name in ("WWP_colorSchemeUtils", "color_scheme_common"):
    try:
        if module_name in sys.modules:
            del sys.modules[module_name]
    except Exception:
        pass

import WWP_colorSchemeUtils as csu
import color_scheme_common as csc

try:
    csu = importlib.reload(csu)
except Exception:
    pass

try:
    csc = importlib.reload(csc)
except Exception:
    pass


def _log(message):
    try:
        print("[Export Color Scheme] {}".format(message))
    except Exception:
        pass


def main():
    doc = revit.doc
    schemes = csu.collect_color_fill_schemes(doc)
    if not schemes:
        ui.uiUtils_alert("No Color Fill Schemes found in this model.", title="Export Color Scheme")
        return

    scheme_pairs = sorted(
        [(csc.scheme_display_name(doc, scheme), scheme) for scheme in schemes],
        key=lambda x: x[0].lower(),
    )
    labels = [label for label, _scheme in scheme_pairs]
    selected = ui.uiUtils_select_indices(
        labels,
        title="Export Color Scheme",
        prompt="Select one color scheme to export:",
        multiselect=False,
        width=760,
        height=560,
    )
    if not selected:
        return

    scheme = scheme_pairs[int(selected[0])][1]
    default_name = "{}.xlsx".format((getattr(scheme, "Name", "") or "Color Scheme").replace("/", "-").replace("\\", "-"))
    path = ui.uiUtils_save_file_dialog(
        title="Export Color Scheme to Excel",
        filter_text="Excel Workbook (*.xlsx)|*.xlsx",
        default_extension="xlsx",
        file_name=default_name,
    )
    if not path:
        return

    _log("Selected scheme: {}".format(csc.describe_scheme(scheme)))
    _log("Export path: {}".format(path))
    payload = csc.build_payload_from_scheme(doc, scheme)
    _log("Payload built. Entry count={}".format(len(payload.get("entries", []))))
    try:
        csc.export_payload_to_excel(payload, path, log=_log)
    except Exception as ex:
        ui.uiUtils_alert("Failed to export color scheme.\n\n{}".format(str(ex)), title="Export Color Scheme")
        return

    ui.uiUtils_alert("Exported scheme '{}' to:\n{}".format(payload.get("scheme_name", "Color Scheme"), path), title="Export Color Scheme")


if __name__ == "__main__":
    main()
