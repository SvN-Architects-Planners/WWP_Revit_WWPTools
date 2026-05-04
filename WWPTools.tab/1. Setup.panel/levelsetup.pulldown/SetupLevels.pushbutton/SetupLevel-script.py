# -*- coding: utf-8 -*-
#! python3
import clr
import re
import traceback

from Autodesk.Revit import DB
import WWP_uiUtils as ui


def _parse_number(text, default_value, cast_int=False):
    if text is None:
        return default_value
    try:
        value = float(str(text).strip())
    except Exception:
        return default_value
    if cast_int:
        return int(round(value))
    return value


def _prompt_inputs(title):
    inputs = None
    if hasattr(ui, "uiUtils_level_setup_inputs"):
        try:
            inputs = ui.uiUtils_level_setup_inputs(
                title=title,
                level_count_label="How many levels are needed?",
                height12_label="Level 1 to Level 2 height (mm):",
                height23_label="Level 2 to Level 3 height (mm):",
                typical_height_label="Typical floor-to-floor height after Level 3 (mm):",
                default_level_count="50",
                default_height12="4500",
                default_height23="4500",
                default_typical_height="3000",
                default_underground_count="3",
                default_height_p1_to_l1="3000",
                default_typical_depth="3000",
                ok_text="OK",
                cancel_text="Cancel",
                width=520,
                height=520,
            )
        except Exception:
            inputs = None

    if inputs is None:
        level_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="How many levels are needed?",
            default_value="50",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if level_text is None:
            return None
        h12_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="Level 1 to Level 2 height (mm):",
            default_value="4500",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if h12_text is None:
            return None
        h23_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="Level 2 to Level 3 height (mm):",
            default_value="4500",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if h23_text is None:
            return None
        typical_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="Typical floor-to-floor height after Level 3 (mm):",
            default_value="3000",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if typical_text is None:
            return None
        underground_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="How many underground levels?",
            default_value="3",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if underground_text is None:
            return None
        p1_l1_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="Floor-to-floor height from P1 to Level 1 (mm):",
            default_value="3000",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if p1_l1_text is None:
            return None
        typical_depth_text = ui.uiUtils_prompt_text(
            title=title,
            prompt="Typical depth below (P2-P3, P3-P4...) (mm):",
            default_value="3000",
            ok_text="OK",
            cancel_text="Cancel",
            width=420,
            height=220,
        )
        if typical_depth_text is None:
            return None
        inputs = {
            "level_count": level_text,
            "height12": h12_text,
            "height23": h23_text,
            "typical_height": typical_text,
            "underground_count": underground_text,
            "height_p1_to_l1": p1_l1_text,
            "typical_depth": typical_depth_text,
        }

    return inputs


def _mm_to_internal(mm_value):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(mm_value), DB.UnitTypeId.Millimeters)
    except Exception:
        try:
            return DB.UnitUtils.ConvertToInternalUnits(float(mm_value), DB.DisplayUnitType.DUT_MILLIMETERS)
        except Exception:
            return float(mm_value) / 304.8


def _is_excluded_level(name):
    if not name:
        return True
    if "1.5" in name:
        return True
    if "P" in name or "p" in name:
        return True
    return False


def _is_parking_level(name):
    if not name:
        return False
    trimmed = name.strip()
    return bool(re.search(r"\b[Pp]\d+\b", trimmed))


def _parse_parking_number(name):
    if not name:
        return None
    match = re.search(r"\b[Pp](\d+)\b", name)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _parse_level_number(name):
    match = re.search(r"\d+", name or "")
    if not match:
        return None
    try:
        return int(match.group(0))
    except Exception:
        return None


def _level_name(number):
    return "FLOOR {:02d}".format(number)


def _parking_level_name(number):
    return "LEVEL P{}".format(number)


def _unique_name(existing_names, base_name):
    if base_name not in existing_names:
        return base_name
    index = 2
    while True:
        candidate = "{} ({})".format(base_name, index)
        if candidate not in existing_names:
            return candidate
        index += 1


def _get_levels(doc):
    return list(DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements())


def _set_level_elevation(level, elevation):
    try:
        level.Elevation = elevation
        return True
    except Exception:
        pass
    try:
        param = level.get_Parameter(DB.BuiltInParameter.LEVEL_ELEV)
        if param and not param.IsReadOnly:
            param.Set(elevation)
            return True
    except Exception:
        pass
    return False


def main():
    uidoc = __revit__.ActiveUIDocument
    if uidoc is None:
        ui.uiUtils_alert("No active Revit document found.", title="Level Setup")
        return

    doc = uidoc.Document

    title = "Level Setup"
    inputs = _prompt_inputs(title)
    if not inputs:
        return

    level_count = _parse_number(inputs.get("level_count"), 50, cast_int=True)
    if level_count < 1:
        level_count = 1

    h12_mm = _parse_number(inputs.get("height12"), 4500, cast_int=False)
    h23_mm = _parse_number(inputs.get("height23"), 4500, cast_int=False)
    typical_mm = _parse_number(inputs.get("typical_height"), 3000, cast_int=False)
    underground_count = _parse_number(inputs.get("underground_count"), 0, cast_int=True)
    if underground_count < 0:
        underground_count = 0
    height_p1_to_l1_mm = _parse_number(inputs.get("height_p1_to_l1"), 4500, cast_int=False)
    typical_depth_mm = _parse_number(inputs.get("typical_depth"), 3000, cast_int=False)

    h12 = _mm_to_internal(h12_mm)
    h23 = _mm_to_internal(h23_mm)
    typical = _mm_to_internal(typical_mm)
    height_p1_to_l1 = _mm_to_internal(height_p1_to_l1_mm)
    typical_depth = _mm_to_internal(typical_depth_mm)

    levels = _get_levels(doc)
    if not levels:
        ui.uiUtils_alert("No levels found in the document.", title=title)
        return

    candidates = []
    parking_candidates = []
    for level in levels:
        name = level.Name or ""
        if _is_parking_level(name):
            number = _parse_parking_number(name)
            if number is not None:
                parking_candidates.append((number, level))
            continue
        if _is_excluded_level(name):
            continue
        number = _parse_level_number(name)
        if number is None:
            continue
        candidates.append((number, level))

    if not candidates:
        ui.uiUtils_alert("No eligible levels found (excluding P levels and 1.5).", title=title)
        return

    levels_by_number = {}
    for number, level in candidates:
        levels_by_number.setdefault(number, []).append(level)

    levels_to_delete = [level for number, level in candidates if number > level_count]
    parking_by_number = {}
    for number, level in parking_candidates:
        parking_by_number.setdefault(number, []).append(level)
    parking_to_delete = [level for number, level in parking_candidates if number > underground_count]
    if levels_to_delete or parking_to_delete:
        delete_names = [lvl.Name for lvl in levels_to_delete + parking_to_delete]
        message = "Delete {} levels above {}?\n\n{}".format(
            len(delete_names),
            level_count,
            "\n".join(delete_names[:20]),
        )
        if len(delete_names) > 20:
            message += "\n... {} more".format(len(delete_names) - 20)
        if not ui.uiUtils_confirm(message, title="Level Setup"):
            levels_to_delete = []
            parking_to_delete = []

    existing_names = {lvl.Name for lvl in levels if getattr(lvl, "Name", None)}

    # Determine base elevation from level 1 if present
    base_elevation = 0.0
    level1_list = levels_by_number.get(1)
    if level1_list:
        level1_list.sort(key=lambda l: l.Elevation)
        base_elevation = level1_list[0].Elevation
    else:
        # create level 1 at elevation 0
        pass

    created = []
    updated = []
    deleted = []

    t = DB.Transaction(doc, "Setup Levels")
    t.Start()
    try:
        # Delete levels above desired count
        for level in levels_to_delete:
            try:
                doc.Delete(level.Id)
                deleted.append(level.Name)
            except Exception:
                pass
        for level in parking_to_delete:
            try:
                doc.Delete(level.Id)
                deleted.append(level.Name)
            except Exception:
                pass

        # Ensure level 1 exists
        if not level1_list:
            lvl1 = DB.Level.Create(doc, base_elevation)
            lvl1.Name = _unique_name(existing_names, _level_name(1))
            existing_names.add(lvl1.Name)
            created.append(lvl1.Name)
            levels_by_number[1] = [lvl1]

        # Update/Create levels 2..N
        current_elevation = base_elevation
        for number in range(2, level_count + 1):
            if number == 2:
                current_elevation = base_elevation + h12
            elif number == 3:
                current_elevation = base_elevation + h12 + h23
            else:
                current_elevation = base_elevation + h12 + h23 + (number - 3) * typical

            level_list = levels_by_number.get(number, [])
            if level_list:
                level_list.sort(key=lambda l: l.Elevation)
                lvl = level_list[0]
                if _set_level_elevation(lvl, current_elevation):
                    updated.append(lvl.Name)
            else:
                lvl = DB.Level.Create(doc, current_elevation)
                lvl.Name = _unique_name(existing_names, _level_name(number))
                existing_names.add(lvl.Name)
                created.append(lvl.Name)

        # Create/Update underground parking levels below Level 1
        if underground_count > 0:
            for number in range(1, underground_count + 1):
                if number == 1:
                    target_elevation = base_elevation - height_p1_to_l1
                else:
                    target_elevation = base_elevation - height_p1_to_l1 - (number - 1) * typical_depth

                level_list = parking_by_number.get(number, [])
                if level_list:
                    level_list.sort(key=lambda l: l.Elevation)
                    lvl = level_list[0]
                if _set_level_elevation(lvl, target_elevation):
                    if lvl.Name != _parking_level_name(number):
                        try:
                            lvl.Name = _unique_name(existing_names, _parking_level_name(number))
                            existing_names.add(lvl.Name)
                        except Exception:
                            pass
                    updated.append(lvl.Name)
                else:
                    lvl = DB.Level.Create(doc, target_elevation)
                    lvl.Name = _unique_name(existing_names, _parking_level_name(number))
                    existing_names.add(lvl.Name)
                    created.append(lvl.Name)

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    summary = [
        "Created: {}".format(len(created)),
        "Updated: {}".format(len(updated)),
        "Deleted: {}".format(len(deleted)),
    ]
    if created:
        summary.append("\nCreated levels:")
        summary.extend(["- {}".format(name) for name in created[:20]])
        if len(created) > 20:
            summary.append("... {} more".format(len(created) - 20))
    if deleted:
        summary.append("\nDeleted levels:")
        summary.extend(["- {}".format(name) for name in deleted[:20]])
        if len(deleted) > 20:
            summary.append("... {} more".format(len(deleted) - 20))

    ui.uiUtils_alert("\n".join(summary), title=title)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        ui.uiUtils_alert(traceback.format_exc(), title="Level Setup - Error")
