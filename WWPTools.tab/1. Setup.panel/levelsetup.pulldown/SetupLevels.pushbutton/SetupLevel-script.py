# -*- coding: utf-8 -*-
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
    return ui.uiUtils_level_setup_inputs(title=title)


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

    start_from_zero = bool(inputs.get("start_from_zero", False))
    start_num = 0 if start_from_zero else 1

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

    levels_to_delete = [level for number, level in candidates if number >= start_num + level_count]
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

    # Determine base elevation from ground level (0 for UK, 1 for Canada)
    base_elevation = 0.0
    base_level_list = levels_by_number.get(start_num)
    if base_level_list:
        base_level_list.sort(key=lambda l: l.Elevation)
        base_elevation = base_level_list[0].Elevation
    else:
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

        # Ensure ground level exists (FLOOR 00 in UK, FLOOR 01 in Canada)
        if not base_level_list:
            lvl_base = DB.Level.Create(doc, base_elevation)
            lvl_base.Name = _unique_name(existing_names, _level_name(start_num))
            existing_names.add(lvl_base.Name)
            created.append(lvl_base.Name)
            levels_by_number[start_num] = [lvl_base]
        else:
            lvl_base = base_level_list[0]
            target_name = _level_name(start_num)
            if lvl_base.Name != target_name:
                try:
                    old_name = lvl_base.Name
                    new_name = _unique_name(existing_names - {old_name}, target_name)
                    lvl_base.Name = new_name
                    existing_names.discard(old_name)
                    existing_names.add(new_name)
                    updated.append(new_name)
                except Exception:
                    pass

        # Update/Create levels above ground
        current_elevation = base_elevation
        for number in range(start_num + 1, start_num + level_count):
            offset = number - start_num
            if offset == 1:
                current_elevation = base_elevation + h12
            elif offset == 2:
                current_elevation = base_elevation + h12 + h23
            else:
                current_elevation = base_elevation + h12 + h23 + (offset - 2) * typical

            level_list = levels_by_number.get(number, [])
            if level_list:
                level_list.sort(key=lambda l: l.Elevation)
                lvl = level_list[0]
                if _set_level_elevation(lvl, current_elevation):
                    target_name = _level_name(number)
                    if lvl.Name != target_name:
                        try:
                            old_name = lvl.Name
                            new_name = _unique_name(existing_names - {old_name}, target_name)
                            lvl.Name = new_name
                            existing_names.discard(old_name)
                            existing_names.add(new_name)
                        except Exception:
                            pass
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
