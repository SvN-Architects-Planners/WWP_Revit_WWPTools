# -*- coding: utf-8 -*-
"""
For each source view on a chosen level, create an equivalent view on the
target level(s), copying the exact ViewFamilyType, ViewTemplate, and all
writable instance parameters (browser-organisation fields, subcategory, etc.).
"""
import re
import traceback

from Autodesk.Revit import DB
import WWP_uiUtils as ui


def _show_level_picker(level_names, title="Duplicate Views For Level"):
    """Single form with source (single-select) and target (multi-select) level lists."""
    import clr
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows import Application, ShutdownMode
    from System.Windows.Markup import XamlReader
    from System.Windows.Interop import WindowInteropHelper
    from System import IntPtr
    from System.Diagnostics import Process

    if Application.Current is None:
        app = Application()
        app.ShutdownMode = ShutdownMode.OnExplicitShutdown

    xaml = (
        '<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"'
        ' Title="" Width="400" Height="540" WindowStartupLocation="CenterScreen" ResizeMode="CanResize">'
        '<Grid Margin="16,14,16,14">'
        '<Grid.RowDefinitions>'
        '<RowDefinition Height="Auto"/>'
        '<RowDefinition Height="*"/>'
        '<RowDefinition Height="12"/>'
        '<RowDefinition Height="Auto"/>'
        '<RowDefinition Height="Auto"/>'
        '<RowDefinition Height="*"/>'
        '<RowDefinition Height="12"/>'
        '<RowDefinition Height="Auto"/>'
        '</Grid.RowDefinitions>'
        '<TextBlock Grid.Row="0" Text="Get level style from:" FontWeight="SemiBold" Margin="0,0,0,4"/>'
        '<ListBox Grid.Row="1" Name="SourceList" SelectionMode="Single"/>'
        '<TextBlock Grid.Row="3" Text="Target new level:" FontWeight="SemiBold" Margin="0,0,0,2"/>'
        '<TextBlock Grid.Row="4" Text="(Hold Ctrl or Shift to select multiple)" Foreground="Gray" FontSize="11" Margin="0,0,0,4"/>'
        '<ListBox Grid.Row="5" Name="TargetList" SelectionMode="Extended"/>'
        '<StackPanel Grid.Row="7" Orientation="Horizontal" HorizontalAlignment="Right">'
        '<Button Name="OkButton" Content="OK" Width="80" Margin="0,0,8,0" IsDefault="True" IsEnabled="False"/>'
        '<Button Name="CancelButton" Content="Cancel" Width="80" IsCancel="True"/>'
        '</StackPanel>'
        '</Grid>'
        '</Window>'
    )

    window = XamlReader.Parse(xaml)
    window.Title = title
    source_list = window.FindName("SourceList")
    target_list = window.FindName("TargetList")
    ok_btn = window.FindName("OkButton")

    for name in level_names:
        source_list.Items.Add(name)
        target_list.Items.Add(name)

    try:
        owner = Process.GetCurrentProcess().MainWindowHandle
        if owner != IntPtr.Zero:
            WindowInteropHelper(window).Owner = owner
    except Exception:
        pass

    def _update_ok(s, e):
        ok_btn.IsEnabled = (source_list.SelectedIndex >= 0 and target_list.SelectedItems.Count > 0)

    source_list.SelectionChanged += _update_ok
    target_list.SelectionChanged += _update_ok

    def _accept(s, e):
        if ok_btn.IsEnabled:
            window.DialogResult = True

    ok_btn.Click += _accept

    if window.ShowDialog() != True:
        return None, None

    src_idx = source_list.SelectedIndex
    tgt_indices = [target_list.Items.IndexOf(item) for item in target_list.SelectedItems]
    return src_idx, tgt_indices


def _collect_levels(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())
    levels.sort(key=lambda l: l.Elevation)
    return levels


def _collect_area_schemes(doc):
    return list(DB.FilteredElementCollector(doc).OfClass(DB.AreaScheme).ToElements())


def _views_for_level(doc, level):
    """Return all non-template ViewPlan elements whose GenLevel matches the given level."""
    result = []
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
        if v.IsTemplate:
            continue
        gen_level = v.GenLevel
        if gen_level is not None and gen_level.Id == level.Id:
            result.append(v)
    return result


def _derive_name(view_name, src_level_name, tgt_level_name):
    """Derive the target view name by replacing the source level name.

    Strategy 1 - exact or substring match on the full level name string.
    Strategy 2 - fallback: substitute the last numeric token only.
    This correctly handles parking levels (LEVEL P1) where the prefix
    differs from above-ground levels (FLOOR 01).
    """
    if view_name == src_level_name:
        return tgt_level_name
    if src_level_name and src_level_name in view_name:
        return view_name.replace(src_level_name, tgt_level_name, 1)
    # Fallback: number substitution
    src_nums = re.findall(r'\d+', src_level_name)
    tgt_nums = re.findall(r'\d+', tgt_level_name)
    src_num = src_nums[-1] if src_nums else None
    tgt_num = tgt_nums[-1] if tgt_nums else None
    if src_num and tgt_num:
        return re.sub(r'(?<!\d)' + re.escape(src_num) + r'(?!\d)', tgt_num, view_name)
    return view_name


def _copy_writable_params(src, dst):
    """Copy all writable instance parameters from src view to dst view."""
    # Built-in parameters that are set automatically or are meaningless to copy
    _SKIP_BIPS = {
        DB.BuiltInParameter.VIEW_NAME,
        DB.BuiltInParameter.VIEW_DESCRIPTION,   # Title on Sheet / Name on Sheet
        DB.BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM,
        DB.BuiltInParameter.ELEM_FAMILY_PARAM,
        DB.BuiltInParameter.ELEM_TYPE_PARAM,
        DB.BuiltInParameter.PLAN_VIEW_LEVEL,
        DB.BuiltInParameter.VIEW_PHASE,
        DB.BuiltInParameter.VIEW_PHASE_FILTER,
    }
    # Also skip by name in case the param is shared/project and not a BIP
    _SKIP_NAMES = {"View Name", "Name on Sheet", "Title on Sheet"}

    for param in src.Parameters:
        if param.IsReadOnly:
            continue
        if param.StorageType == getattr(DB.StorageType, "None"):
            continue

        defn = param.Definition
        if defn is None:
            continue

        # Skip by built-in parameter enum
        try:
            bip = defn.BuiltInParameter
            if bip in _SKIP_BIPS:
                continue
        except Exception:
            pass

        # Skip by parameter name (catches shared/project params with the same intent)
        if defn.Name in _SKIP_NAMES:
            continue

        try:
            dst_param = dst.LookupParameter(defn.Name)
            if dst_param is None or dst_param.IsReadOnly:
                continue

            st = param.StorageType
            if st == DB.StorageType.String:
                val = param.AsString()
                dst_param.Set(val if val is not None else "")
            elif st == DB.StorageType.Integer:
                dst_param.Set(param.AsInteger())
            elif st == DB.StorageType.Double:
                dst_param.Set(param.AsDouble())
            elif st == DB.StorageType.ElementId:
                eid = param.AsElementId()
                if eid is not None and eid != DB.ElementId.InvalidElementId:
                    dst_param.Set(eid)
        except Exception:
            pass


def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        ui.uiUtils_alert("No active Revit document found.", title="Duplicate Views For Level")
        return

    doc = uidoc.Document
    levels = _collect_levels(doc)
    if not levels:
        ui.uiUtils_alert("No levels found in the document.", title="Duplicate Views For Level")
        return

    level_names = [lvl.Name for lvl in levels]

    # --- Single form: pick source and target levels ---
    src_idx, tgt_indices = _show_level_picker(level_names)
    if src_idx is None:
        return
    source_level = levels[src_idx]
    target_levels = [levels[i] for i in (tgt_indices or []) if levels[i].Id != source_level.Id]
    if not target_levels:
        ui.uiUtils_alert("No valid target levels selected.", title="Duplicate Views For Level")
        return

    source_views = _views_for_level(doc, source_level)
    if not source_views:
        ui.uiUtils_alert(
            "No views found associated with '{}'.".format(source_level.Name),
            title="Duplicate Views For Level",
        )
        return

    # --- Create views ---
    created = []
    failed = []
    skipped = []

    t = DB.Transaction(doc, "Duplicate Views For Level")
    t.Start()
    try:
        for target_level in target_levels:
            # Track existing names per view type so floor plans and ceiling plans
            # don't block each other (they can coexist with the same name in Revit).
            existing_by_type = {}
            existing_area_schemes = set()
            for ev in _views_for_level(doc, target_level):
                existing_by_type.setdefault(ev.ViewType, set()).add(ev.Name)
                if ev.ViewType == DB.ViewType.AreaPlan and ev.AreaScheme is not None:
                    existing_area_schemes.add(ev.AreaScheme.Id.IntegerValue)

            for src_view in source_views:
                vtype = src_view.ViewType

                derived = _derive_name(src_view.Name, source_level.Name, target_level.Name)

                # Skip if a view with that derived name already exists on the target
                # (checked per view type, not globally).
                if vtype == DB.ViewType.AreaPlan:
                    src_scheme = src_view.AreaScheme
                    scheme_id = src_scheme.Id.IntegerValue if src_scheme else -1
                    if scheme_id in existing_area_schemes:
                        skipped.append("'{}' already exists on '{}'".format(derived, target_level.Name))
                        continue
                else:
                    type_names = existing_by_type.get(vtype, set())
                    if derived in type_names:
                        skipped.append("'{}' already exists on '{}'".format(derived, target_level.Name))
                        continue

                try:
                    if vtype in (DB.ViewType.FloorPlan, DB.ViewType.CeilingPlan):
                        new_view = DB.ViewPlan.Create(doc, src_view.GetTypeId(), target_level.Id)

                    elif vtype == DB.ViewType.AreaPlan:
                        if src_scheme is None:
                            failed.append("No area scheme on source view '{}'".format(src_view.Name))
                            continue
                        new_view = DB.ViewPlan.CreateAreaPlan(doc, src_scheme.Id, target_level.Id)

                    else:
                        skipped.append("'{}' - unsupported type ({})".format(src_view.Name, vtype))
                        continue

                    # Track so subsequent iterations in the same run don't re-create.
                    existing_by_type.setdefault(vtype, set()).add(derived)
                    if vtype == DB.ViewType.AreaPlan:
                        existing_area_schemes.add(scheme_id)

                    # Apply derived name.
                    if derived != src_view.Name:
                        try:
                            new_view.Name = derived
                        except Exception:
                            pass
                    final_name = new_view.Name

                    # Apply view template.
                    tmpl_id = src_view.ViewTemplateId
                    if tmpl_id and tmpl_id != DB.ElementId.InvalidElementId:
                        try:
                            new_view.ViewTemplateId = tmpl_id
                        except Exception:
                            pass

                    # Copy all writable instance parameters.
                    _copy_writable_params(src_view, new_view)

                    created.append("{} -> {}".format(src_view.Name, final_name))

                except Exception as ex:
                    failed.append("'{}' on '{}': {}".format(src_view.Name, target_level.Name, str(ex)))

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    summary = [
        "Source: {} ({} views)".format(source_level.Name, len(source_views)),
        "Target(s): {}".format(", ".join(l.Name for l in target_levels)),
        "",
        "Created: {}".format(len(created)),
        "Failed:  {}".format(len(failed)),
        "Skipped: {}".format(len(skipped)),
    ]
    if created:
        summary.append("\nCreated:")
        summary.extend("  " + n for n in created[:30])
        if len(created) > 30:
            summary.append("  ... {} more".format(len(created) - 30))
    if failed:
        summary.append("\nFailed:")
        summary.extend("  " + n for n in failed[:20])
    if skipped:
        summary.append("\nSkipped:")
        summary.extend("  " + n for n in skipped[:20])

    ui.uiUtils_alert("\n".join(summary), title="Duplicate Views For Level")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="Duplicate Views For Level - Error")
