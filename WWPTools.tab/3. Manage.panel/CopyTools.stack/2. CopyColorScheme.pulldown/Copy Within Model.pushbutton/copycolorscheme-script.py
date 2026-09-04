# -*- coding: utf-8 -*-

import os
import sys
import importlib
from pyrevit import revit, script
import WWP_uiUtils as ui
from System.IO import File
from System.Windows import Visibility
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

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

import WWP_colorSchemeUtils as csu
import color_scheme_common as csc
from WWP_versioning import apply_window_title

try:
    csu = importlib.reload(csu)
except Exception:
    pass


output = script.get_output()


def _log(message):
    try:
        print("[Copy Color Scheme] {}".format(message))
    except Exception:
        pass


def _scheme_display_name(doc, scheme):
    return csc.scheme_display_name(doc, scheme)


def _describe_scheme(scheme):
    return csc.describe_scheme(scheme)


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = revit.uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _show_selection_dialog(display_names):
    xaml_path = os.path.join(script_dir, "CopyColorSchemeSetup.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    ui.uiUtils_ensure_theme()
    window = XamlReader.Parse(xaml_text)
    apply_window_title(window, "Copy Color Scheme")
    _set_owner(window)

    source_combo = window.FindName("SourceSchemeCombo")
    target_combo = window.FindName("TargetSchemeCombo")
    create_new_check = window.FindName("CreateNewSchemeCheck")
    validation = window.FindName("ValidationText")
    ok_btn = window.FindName("OkButton")
    cancel_btn = window.FindName("CancelButton")

    for name in display_names:
        source_combo.Items.Add(name)
        target_combo.Items.Add(name)

    if source_combo.Items.Count > 0:
        source_combo.SelectedIndex = 0
    if target_combo.Items.Count > 1:
        target_combo.SelectedIndex = 1
    elif target_combo.Items.Count > 0:
        target_combo.SelectedIndex = 0

    result = {"ok": False}

    def _set_validation(msg):
        if msg:
            validation.Text = msg
            validation.Visibility = Visibility.Visible
        else:
            validation.Text = ""
            validation.Visibility = Visibility.Collapsed

    def _on_ok(sender, args):
        source_idx = int(source_combo.SelectedIndex)
        target_idx = int(target_combo.SelectedIndex)
        create_new = bool(getattr(create_new_check, "IsChecked", False)) if create_new_check else False
        if source_idx < 0 or target_idx < 0:
            _set_validation("Please select both source and target schemes.")
            return
        if source_idx == target_idx and not create_new:
            _set_validation("Source and target must be different schemes.")
            return
        result["ok"] = True
        result["source_index"] = source_idx
        result["target_index"] = target_idx
        result["create_new"] = create_new
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    ok_btn.Click += _on_ok
    cancel_btn.Click += _on_cancel
    if window.ShowDialog() != True:
        return None
    return result if result.get("ok") else None


def main():
    doc = revit.doc
    schemes = csu.collect_color_fill_schemes(doc)
    if not schemes:
        ui.uiUtils_alert("No Color Fill Schemes found in this model.", title="Copy Color Scheme")
        return

    names = [_scheme_display_name(doc, s) for s in schemes]
    pick = _show_selection_dialog(names)
    if not pick:
        return

    source = schemes[int(pick["source_index"])]
    selected_target = schemes[int(pick["target_index"])]
    create_new = bool(pick.get("create_new", False))
    _log("Starting copy.")
    _log("Source: {}".format(_describe_scheme(source)))
    _log("Selected target: {}".format(_describe_scheme(selected_target)))
    _log("Mode: {}".format("create new scheme in target scope" if create_new else "overwrite selected target scheme"))

    with revit.Transaction("Copy Color Scheme"):
        source_name = (getattr(source, "Name", "") or "Color Scheme").strip()
        target = selected_target

        if create_new:
            new_name = csc.unique_scheme_name_in_scope(doc, selected_target, source_name)
            _log("Creating new target scheme '{}' from scope seed '{}'.".format(new_name, getattr(selected_target, "Name", "<unnamed>")))
            new_id = selected_target.Duplicate(new_name)
            created = doc.GetElement(new_id)
            if created is None:
                ui.uiUtils_alert(
                    "Failed to create new scheme '{}' in selected target scope.".format(new_name),
                    title="Copy Color Scheme",
                )
                return
            target = created
            _log("Created target: {}".format(_describe_scheme(target)))
        else:
            _log("Overwriting selected target directly: {}".format(_describe_scheme(target)))

        ok, error = csu.copy_scheme_data(source, target, log=_log)

    if not ok:
        ui.uiUtils_alert("Failed to update target Color Scheme.\n\n{}".format(error or "Unknown error"), title="Copy Color Scheme")
        return

    try:
        with revit.Transaction("Finalize Copy Color Scheme"):
            _ok2, _err2 = csu.force_overwrite_scheme_visuals(source, target, log=_log)
    except Exception:
        _ok2, _err2 = True, None

    try:
        revit.get_selection().set_to([target.Id])
    except Exception:
        pass

    if not _ok2:
        ui.uiUtils_alert(
            "Updated scheme, but some 'In Use' entry visuals may not have overwritten.\n\n{}".format(_err2 or "Unknown error"),
            title="Copy Color Scheme",
        )
        return

    _log("Completed successfully. Final target: {}".format(_describe_scheme(target)))
    ui.uiUtils_alert("Updated scheme: {}".format(getattr(target, "Name", "Color Scheme")), title="Copy Color Scheme")


if __name__ == "__main__":
    main()
