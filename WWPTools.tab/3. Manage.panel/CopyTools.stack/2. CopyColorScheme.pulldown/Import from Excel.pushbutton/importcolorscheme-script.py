# -*- coding: utf-8 -*-

import clr
import os
import sys
import importlib
from System.Collections.Generic import List
from System.IO import File
clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
from System.Windows import Visibility
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from Autodesk.Revit import DB
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


TITLE = "Import Color Scheme"


def _log(message):
    try:
        print("[Import Color Scheme] {}".format(message))
    except Exception:
        pass


def _elem_id_int(elem_id):
    if elem_id is None:
        return None
    try:
        return int(elem_id.Value)  # Revit 2024+ uses .Value
    except Exception:
        pass
    try:
        return int(elem_id.IntegerValue)  # Revit 2023 and earlier
    except Exception:
        pass
    try:
        return int(elem_id)
    except Exception:
        return None


def _category_name(doc, category_id):
    if category_id is None:
        return "Unknown Category"
    cat_int = _elem_id_int(category_id)
    try:
        categories = getattr(getattr(doc, "Settings", None), "Categories", None)
        if categories is not None:
            cat = categories.get_Item(category_id)
            cat_name = getattr(cat, "Name", "") if cat else ""
            if cat_name:
                return cat_name
    except Exception:
        pass
    try:
        import System
        bic = System.Enum.ToObject(DB.BuiltInCategory, cat_int)
        categories = getattr(getattr(doc, "Settings", None), "Categories", None)
        if categories is not None:
            cat = categories.get_Item(bic)
            cat_name = getattr(cat, "Name", "") if cat else ""
            if cat_name:
                return cat_name
        label = DB.LabelUtils.GetLabelFor(bic)
        if label:
            return label
    except Exception:
        pass
    try:
        label = DB.LabelUtils.GetLabelFor(category_id)
        if label:
            return label
    except Exception:
        pass
    if cat_int is not None:
        return "Category {}".format(cat_int)
    return "Unknown Category"


def _scope_label(doc, scheme):
    area_name = csc.scheme_area_scheme_name(doc, scheme)
    if area_name:
        return "Area({})".format(area_name)
    category_label = _category_name(doc, getattr(scheme, "CategoryId", None))
    if not category_label or category_label == "Unknown Category":
        return ""
    return category_label


def _build_choices(doc, schemes):
    for scheme in schemes:
        if _scope_label(doc, scheme) == "":
            _log(
                "Unresolved category for scheme '{}' categoryId={} areaSchemeId={}".format(
                    getattr(scheme, "Name", "") or "Color Scheme",
                    _elem_id_int(getattr(scheme, "CategoryId", None)),
                    _elem_id_int(csc.scheme_area_scheme_id(scheme)),
                )
            )
    targets = [{
        "label": (
            "{}: {}".format(_scope_label(doc, scheme), getattr(scheme, "Name", "") or "Color Scheme")
            if _scope_label(doc, scheme)
            else (getattr(scheme, "Name", "") or "Color Scheme")
        ),
        "scheme": scheme,
    } for scheme in schemes]
    targets.sort(key=lambda x: x["label"].lower())
    return targets


def _format_payload_snapshot(payload):
    storage_types = sorted(set([
        str(item.get("storage_type", "")).strip()
        for item in payload.get("entries", [])
        if str(item.get("storage_type", "")).strip()
    ]))
    lines = [
        "Workbook",
        "Scheme: {}".format(payload.get("scheme_name", "")),
        "Category: {}".format(payload.get("category_name", "")),
        "Area Scheme: {}".format(payload.get("area_scheme_name", "")),
        "Title: {}".format(payload.get("title", "")),
        "Parameter: {} ({})".format(payload.get("parameter_name", ""), payload.get("parameter_id", "")),
        "Modes: ByValue={} ByRange={} ByPercentage={}".format(
            bool(payload.get("is_by_value")),
            bool(payload.get("is_by_range")),
            bool(payload.get("is_by_percentage")),
        ),
        "Entry Storage Types: {}".format(", ".join(storage_types) if storage_types else "<none>"),
        "Entry Count: {}".format(len(payload.get("entries", []))),
    ]
    return "\n".join(lines)


def _format_target_snapshot(snapshot):
    lines = [
        "Target Scheme",
        "Scheme: {}".format(snapshot.get("scheme_name", "")),
        "Category: {}".format(snapshot.get("category_name", "")),
        "Area Scheme: {}".format(snapshot.get("area_scheme_name", "")),
        "Title: {}".format(snapshot.get("title", "")),
        "Parameter: {} ({})".format(snapshot.get("parameter_name", ""), snapshot.get("parameter_id", "")),
        "Modes: ByValue={} ByRange={} ByPercentage={}".format(
            bool(snapshot.get("is_by_value")),
            bool(snapshot.get("is_by_range")),
            bool(snapshot.get("is_by_percentage")),
        ),
        "Entry Storage Types: {}".format(", ".join(snapshot.get("entry_storage_types", [])) if snapshot.get("entry_storage_types") else "<none>"),
    ]
    return "\n".join(lines)


def _set_owner(window, uidoc):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = uidoc.Application.MainWindowHandle
    except Exception:
        pass


def _build_sheet_preview(sheet):
    column_headers = [col.get("header", "") or col.get("letter", "") for col in sheet.get("columns", [])]
    if not any(column_headers):
        column_headers = [col.get("label", "") for col in sheet.get("columns", [])]

    lines = [
        "Worksheet: {}".format(sheet.get("name", "")),
        "Rows: {}".format(sheet.get("row_count", 0)),
        "",
        "Headers",
        " | ".join(column_headers) if column_headers else "<none>",
        "",
        "Preview",
    ]
    preview_rows = sheet.get("preview_rows", [])
    if not preview_rows:
        lines.append("<no data rows found>")
    else:
        for row in preview_rows:
            lines.append(" | ".join(row))
    return "\n".join(lines)


def _find_column_index(columns, keywords, fallback_index=0):
    lowered = [((col.get("header", "") or "").strip().lower(), idx) for idx, col in enumerate(columns)]
    for keyword in keywords:
        for header, idx in lowered:
            if keyword in header:
                return idx
    if columns:
        return max(0, min(fallback_index, len(columns) - 1))
    return -1


def _show_mapping_dialog(uidoc, workbook_info):
    xaml_path = os.path.join(script_dir, "ImportColorSchemeMapping.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    window = XamlReader.Parse(File.ReadAllText(xaml_path))
    _set_owner(window, uidoc)

    sheet_combo = window.FindName("WorksheetCombo")
    value_combo = window.FindName("ValueColumnCombo")
    color_combo = window.FindName("ColorColumnCombo")
    use_fill_check = window.FindName("UseCellFillCheck")
    color_override_check = window.FindName("ColorOverrideCheck")
    preview_box = window.FindName("PreviewText")
    validation = window.FindName("ValidationText")
    ok_btn = window.FindName("OkButton")
    cancel_btn = window.FindName("CancelButton")

    sheets = workbook_info.get("sheets", [])
    for sheet in sheets:
        sheet_combo.Items.Add(sheet.get("name", "Sheet"))

    result = {"ok": False}

    def _set_validation(message):
        if message:
            validation.Text = message
            validation.Visibility = Visibility.Visible
        else:
            validation.Text = ""
            validation.Visibility = Visibility.Collapsed

    def _refresh_sheet():
        idx = int(sheet_combo.SelectedIndex)
        if idx < 0 or idx >= len(sheets):
            return

        sheet = sheets[idx]
        columns = sheet.get("columns", [])
        value_combo.Items.Clear()
        color_combo.Items.Clear()
        for col in columns:
            label = col.get("label", "")
            value_combo.Items.Add(label)
            color_combo.Items.Add(label)

        value_idx = _find_column_index(columns, ("value", "name", "number", "code"), 0)
        color_idx = _find_column_index(columns, ("color", "colour", "rgb", "fill"), min(1, len(columns) - 1))

        if value_idx >= 0:
            value_combo.SelectedIndex = value_idx
        if color_idx >= 0:
            color_combo.SelectedIndex = color_idx

        preview_box.Text = _build_sheet_preview(sheet)
        _set_validation("")

    def _on_sheet_changed(sender, args):
        _refresh_sheet()

    def _on_ok(sender, args):
        sheet_idx = int(sheet_combo.SelectedIndex)
        if sheet_idx < 0 or sheet_idx >= len(sheets):
            _set_validation("Select a worksheet.")
            return
        if int(value_combo.SelectedIndex) < 0:
            _set_validation("Select the Excel column that contains the scheme values.")
            return
        if int(color_combo.SelectedIndex) < 0:
            _set_validation("Select the Excel column that contains the colors.")
            return

        result["ok"] = True
        result["sheet_name"] = sheets[sheet_idx].get("name", "")
        result["value_column_index"] = int(value_combo.SelectedIndex)
        result["color_column_index"] = int(color_combo.SelectedIndex)
        result["label_column_index"] = int(value_combo.SelectedIndex)
        result["use_fill_color"] = bool(getattr(use_fill_check, "IsChecked", False))
        result["color_override_only"] = bool(getattr(color_override_check, "IsChecked", False))
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    sheet_combo.SelectionChanged += _on_sheet_changed
    ok_btn.Click += _on_ok
    cancel_btn.Click += _on_cancel

    if sheets:
        sheet_combo.SelectedIndex = 0
        _refresh_sheet()

    if window.ShowDialog() != True:
        return None
    return result if result.get("ok") else None


def _default_target_index(doc, choices, payload=None):
    if not choices:
        return 0

    payload_area = (payload.get("area_scheme_name", "") or "").strip().lower() if payload else ""
    payload_category = (payload.get("category_name", "") or "").strip().lower() if payload else ""
    payload_scheme = (payload.get("scheme_name", "") or "").strip().lower() if payload else ""

    for idx, choice in enumerate(choices):
        scheme = choice.get("scheme")
        if scheme is None:
            continue
        area_name = (csc.scheme_area_scheme_name(doc, scheme) or "").strip().lower()
        category_name = (_category_name(doc, getattr(scheme, "CategoryId", None)) or "").strip().lower()
        scheme_name = (getattr(scheme, "Name", "") or "").strip().lower()

        if payload_area and area_name == payload_area and payload_scheme and scheme_name == payload_scheme:
            return idx
        if payload_area and area_name == payload_area:
            return idx
        if (not payload_area) and payload_category and category_name == payload_category and payload_scheme and scheme_name == payload_scheme:
            return idx
        if (not payload_area) and payload_category and category_name == payload_category:
            return idx
    return 0


def _show_target_dialog(uidoc, doc, choices, payload=None):
    xaml = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Import Color Scheme"
        Width="860"
        Height="640"
        MinWidth="760"
        MinHeight="560"
        WindowStartupLocation="CenterScreen"
        ResizeMode="CanResizeWithGrip"
        FontFamily="Segoe UI"
        FontSize="14"
        Background="#F3F5F7">
    <Grid Margin="16">
        <Border Background="#FFFFFF"
                BorderBrush="#D7DEE6"
                BorderThickness="1"
                Padding="20">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>
                <TextBlock Grid.Row="0"
                           Text="Select the target color scheme. Leave Create New off to overwrite the selected scheme."
                           TextWrapping="Wrap"
                           Foreground="#374151"
                           Margin="0,0,0,12"/>
                <CheckBox x:Name="CreateNewCheck"
                          Grid.Row="1"
                          IsChecked="False"
                          Margin="0,0,0,12"
                          Content="Create new scheme by duplicating the selected target before import" />
                <ListBox x:Name="TargetList"
                         Grid.Row="2"
                         BorderBrush="#CBD5E1"
                         BorderThickness="1"
                         Background="#FFFFFF"
                         Margin="0,0,0,12"/>
                <TextBlock x:Name="SummaryText"
                           Grid.Row="3"
                           TextWrapping="Wrap"
                           Foreground="#4B5563"
                           Margin="0,0,0,12"/>
                <StackPanel Grid.Row="4"
                            Orientation="Horizontal"
                            HorizontalAlignment="Right">
                    <Button x:Name="OkButton"
                            Width="130"
                            Height="28"
                            Margin="0,0,8,0"
                            Content="Continue"/>
                    <Button x:Name="CancelButton"
                            Width="130"
                            Height="28"
                            Content="Cancel"/>
                </StackPanel>
            </Grid>
        </Border>
    </Grid>
</Window>
"""
    window = XamlReader.Parse(xaml)
    _set_owner(window, uidoc)

    target_list = window.FindName("TargetList")
    create_new_check = window.FindName("CreateNewCheck")
    summary_text = window.FindName("SummaryText")
    ok_btn = window.FindName("OkButton")
    cancel_btn = window.FindName("CancelButton")

    for choice in choices:
        target_list.Items.Add(choice.get("label", "Color Scheme"))

    if choices:
        target_list.SelectedIndex = max(0, min(_default_target_index(doc, choices, payload=payload), len(choices) - 1))

    result = {"ok": False}

    def _refresh_summary(sender=None, args=None):
        idx = int(target_list.SelectedIndex)
        if idx < 0 or idx >= len(choices):
            summary_text.Text = "Select a target scheme."
            return
        scheme = choices[idx].get("scheme")
        summary_text.Text = "Mode: {} | Scheme: {} | Scope: {}".format(
            "create new" if bool(getattr(create_new_check, "IsChecked", False)) else "overwrite",
            getattr(scheme, "Name", "Color Scheme"),
            _scope_label(doc, scheme),
        )

    def _on_ok(sender, args):
        idx = int(target_list.SelectedIndex)
        if idx < 0 or idx >= len(choices):
            summary_text.Text = "Select a target scheme."
            return
        choice = dict(choices[idx])
        choice["mode"] = "create" if bool(getattr(create_new_check, "IsChecked", False)) else "overwrite"
        result["ok"] = True
        result["choice"] = choice
        window.DialogResult = True
        window.Close()

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    target_list.SelectionChanged += _refresh_summary
    create_new_check.Checked += _refresh_summary
    create_new_check.Unchecked += _refresh_summary
    ok_btn.Click += _on_ok
    cancel_btn.Click += _on_cancel
    _refresh_summary()

    if window.ShowDialog() != True:
        return None
    return result.get("choice") if result.get("ok") else None


def _run_color_override_transaction(doc, choice, target, color_map):
    _log("UI selection: '{}'".format(choice.get("label", "<unknown>")))
    _log("Mode: {} | Selected scheme: '{}' (Id={}) | Scope: {}".format(
        choice["mode"],
        getattr(target, "Name", "<unknown>"),
        _elem_id_int(getattr(target, "Id", None)),
        _scope_label(doc, target),
    ))
    transaction = DB.Transaction(doc, "Import Color Scheme Colors From Excel")
    transaction.Start()
    try:
        if choice["mode"] == "create":
            new_name = csc.unique_scheme_name_in_scope(doc, target, "Color Override")
            new_id = target.Duplicate(new_name)
            target = doc.GetElement(new_id)
            if target is None:
                ui.uiUtils_alert("Failed to create a new color scheme in the selected target scope.", title=TITLE)
                transaction.RollBack()
                return None, False, "Failed to create new scheme."
            _log("Created new scheme: '{}' (Id={})".format(
                getattr(target, "Name", "<unknown>"),
                _elem_id_int(getattr(target, "Id", None)),
            ))
        else:
            _log("Overwriting existing scheme: '{}' (Id={})".format(
                getattr(target, "Name", "<unknown>"),
                _elem_id_int(getattr(target, "Id", None)),
            ))
        ok, error = csc.apply_color_map_to_scheme(target, color_map, log=_log)
        if ok:
            transaction.Commit()
        else:
            transaction.RollBack()
        return target, ok, error
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise


def _run_import_transaction(doc, choice, target, payload):
    imported_name = payload.get("scheme_name", "Imported Color Scheme")
    transaction = DB.Transaction(doc, "Import Color Scheme From Excel")
    transaction.Start()
    try:
        if choice["mode"] == "create":
            new_name = csc.unique_scheme_name_in_scope(doc, target, imported_name)
            new_id = target.Duplicate(new_name)
            target = doc.GetElement(new_id)
            if target is None:
                ui.uiUtils_alert("Failed to create a new color scheme in the selected target scope.", title=TITLE)
                transaction.RollBack()
                return None, False, "Failed to create new scheme."
        ok, error = csc.apply_payload_to_scheme(target, payload, log=_log)
        if ok:
            transaction.Commit()
        else:
            transaction.RollBack()
        return target, ok, error
    except Exception:
        try:
            transaction.RollBack()
        except Exception:
            pass
        raise


def main():
    uidoc = getattr(__revit__, "ActiveUIDocument", None)
    doc = uidoc.Document if uidoc is not None else None
    if doc is None:
        ui.uiUtils_alert("No active Revit document found.", title=TITLE)
        return

    path = ui.uiUtils_open_file_dialog(
        title="Import Color Scheme from Excel",
        filter_text="Excel Files (*.xlsx;*.xlsm;*.xls)|*.xlsx;*.xlsm;*.xls|All Files (*.*)|*.*",
        multiselect=False,
    )
    if not path:
        return

    schemes = csu.collect_color_fill_schemes(doc)
    if not schemes:
        ui.uiUtils_alert("No Color Fill Schemes found in this model.", title=TITLE)
        return

    choices = _build_choices(doc, schemes)
    if not choices:
        ui.uiUtils_alert("No target categories or schemes are available for import.", title=TITLE)
        return

    structured_payload = None
    structured_error = None
    try:
        structured_payload = csc.import_payload_from_excel(path)
    except Exception as ex:
        structured_error = str(ex)

    if structured_payload is not None:
        workbook_summary = _format_payload_snapshot(structured_payload)
        if not ui.uiUtils_show_text_report(
            TITLE,
            workbook_summary,
            ok_text="Continue",
            cancel_text="Cancel",
            width=760,
            height=420,
        ):
            return

        choice = _show_target_dialog(uidoc, doc, choices, payload=structured_payload)
        if not choice:
            return
        target = choice["scheme"]
        ok_preflight, problems, target_snapshot = csc.validate_payload_against_scheme(doc, structured_payload, target)
        _log("Structured workbook preflight target: {}".format(target_snapshot))
        comparison_text = _format_payload_snapshot(structured_payload) + "\n\n" + _format_target_snapshot(target_snapshot)
        if problems:
            comparison_text += "\n\nMismatch\n" + "\n".join(problems)
            ui.uiUtils_show_text_report(
                TITLE,
                comparison_text,
                ok_text="Close",
                cancel_text=None,
                width=860,
                height=560,
            )
            return
        if not ui.uiUtils_show_text_report(
            TITLE,
            comparison_text,
            ok_text="Import",
            cancel_text="Cancel",
            width=860,
            height=560,
        ):
            return

        target, ok, error = _run_import_transaction(doc, choice, target, structured_payload)
        if not ok:
            ui.uiUtils_alert("Failed to import color scheme.\n\n{}".format(error or "Unknown error"), title=TITLE)
            return
    else:
        if structured_error:
            _log("Structured import parser did not match workbook: {}".format(structured_error))

        choice = _show_target_dialog(uidoc, doc, choices, payload=None)
        if not choice:
            return
        target = choice["scheme"]
        target_snapshot = csc.get_scheme_definition_snapshot(doc, target)

        try:
            workbook_info = csc.inspect_excel_workbook(path)
        except Exception as ex:
            ui.uiUtils_alert("Failed to inspect Excel workbook.\n\n{}".format(str(ex)), title=TITLE)
            return

        mapping = _show_mapping_dialog(uidoc, workbook_info)
        if not mapping:
            return
        mapping["doc"] = doc
        mapping["target_scheme"] = target

        if mapping.get("color_override_only"):
            try:
                color_map = csc.build_color_map_from_excel(path, mapping)
            except Exception as ex:
                ui.uiUtils_alert("Failed to read colors from Excel.\n\n{}".format(str(ex)), title=TITLE)
                return

            summary = "Color Override from Excel\n\nEntries in Excel: {}\nTarget scheme: {}".format(
                len(color_map),
                getattr(target, "Name", "Color Scheme"),
            )
            if not ui.uiUtils_show_text_report(
                TITLE,
                summary,
                ok_text="Apply Color Override",
                cancel_text="Cancel",
                width=760,
                height=300,
            ):
                return

            target, ok, error = _run_color_override_transaction(doc, choice, target, color_map)
            if not ok:
                ui.uiUtils_alert("Failed to apply color override.\n\n{}".format(error or "Unknown error"), title=TITLE)
                return
        else:
            try:
                payload = csc.build_payload_from_mapped_excel(path, mapping, target_snapshot)
            except Exception as ex:
                ui.uiUtils_alert("Failed to build mapped color scheme import.\n\n{}".format(str(ex)), title=TITLE)
                return

            comparison_text = _format_payload_snapshot(payload) + "\n\n" + _format_target_snapshot(target_snapshot)
            if not ui.uiUtils_show_text_report(
                TITLE,
                comparison_text,
                ok_text="Import",
                cancel_text="Cancel",
                width=860,
                height=560,
            ):
                return

            transaction = DB.Transaction(doc, "Merge Color Scheme From Excel")
            transaction.Start()
            try:
                if choice["mode"] == "create":
                    new_name = csc.unique_scheme_name_in_scope(doc, target, payload.get("scheme_name", "Imported Color Scheme"))
                    new_id = target.Duplicate(new_name)
                    target = doc.GetElement(new_id)
                    if target is None:
                        ui.uiUtils_alert("Failed to create a new color scheme in the selected target scope.", title=TITLE)
                        transaction.RollBack()
                        return
                ok, error = csc.merge_payload_into_scheme(target, payload, log=_log)
                if ok:
                    transaction.Commit()
                else:
                    transaction.RollBack()
            except Exception:
                try:
                    transaction.RollBack()
                except Exception:
                    pass
                raise
            if not ok:
                ui.uiUtils_alert("Failed to import color scheme.\n\n{}".format(error or "Unknown error"), title=TITLE)
                return

    try:
        if uidoc is not None:
            selected_ids = List[DB.ElementId]()
            selected_ids.Add(target.Id)
            uidoc.Selection.SetElementIds(selected_ids)
    except Exception:
        pass

    ui.uiUtils_alert("Imported scheme to '{}'.".format(getattr(target, "Name", "Color Scheme")), title=TITLE)


if __name__ == "__main__":
    main()
