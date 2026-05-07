#!python3
# -*- coding: utf-8 -*-

from pyrevit import DB

try:
    from System import Int64
except Exception:
    Int64 = int

try:
    from System.Collections.Generic import List
except Exception:
    List = None


def _log(log, message):
    if callable(log):
        try:
            log(message)
        except Exception:
            pass


def _to_entry_collection(entries):
    if List is None:
        return list(entries)
    net_list = List[DB.ColorFillSchemeEntry]()
    for entry in entries:
        net_list.Add(entry)
    return net_list


def _elem_id_int(elem_id):
    if elem_id is None:
        return None
    try:
        return int(elem_id.Value)  # Revit 2024+
    except Exception:
        pass
    try:
        return int(elem_id.IntegerValue)  # Revit 2023-
    except Exception:
        pass
    try:
        return int(elem_id)
    except Exception:
        return None


def _to_element_id(value):
    if isinstance(value, DB.ElementId):
        return value
    raw_value = int(value)
    try:
        return DB.ElementId(Int64(raw_value))
    except Exception:
        return DB.ElementId(raw_value)


def _scheme_area_scheme_id(scheme):
    try:
        area_scheme_id = getattr(scheme, "AreaSchemeId", None)
        if area_scheme_id and area_scheme_id != DB.ElementId.InvalidElementId:
            return area_scheme_id
    except Exception:
        pass
    try:
        getter = getattr(scheme, "GetAreaSchemeId", None)
        if callable(getter):
            area_scheme_id = getter()
            if area_scheme_id and area_scheme_id != DB.ElementId.InvalidElementId:
                return area_scheme_id
    except Exception:
        pass
    return None


def _same_scope(a, b):
    if a is None or b is None:
        return False
    return (
        _elem_id_int(getattr(a, "CategoryId", None)) == _elem_id_int(getattr(b, "CategoryId", None))
        and _elem_id_int(_scheme_area_scheme_id(a)) == _elem_id_int(_scheme_area_scheme_id(b))
    )


def _collect_color_fill_schemes(doc):
    try:
        schemes = list(DB.FilteredElementCollector(doc).OfClass(DB.ColorFillScheme).ToElements())
    except Exception:
        schemes = []
    return [s for s in schemes if s is not None]


def collect_color_fill_schemes(doc):
    return _collect_color_fill_schemes(doc)


def _find_scheme_in_scope_by_name(all_schemes, scope_seed_scheme, name):
    target_name = (name or "").strip().lower()
    if not target_name:
        return None
    for s in all_schemes:
        try:
            if not _same_scope(s, scope_seed_scheme):
                continue
            if (getattr(s, "Name", "") or "").strip().lower() == target_name:
                return s
        except Exception:
            continue
    return None


def find_scheme_in_scope_by_name(all_schemes, scope_seed_scheme, name):
    return _find_scheme_in_scope_by_name(all_schemes, scope_seed_scheme, name)


def _entry_get_value(entry, storage_type):
    try:
        if storage_type == DB.StorageType.String:
            for name in ("GetStringValue", "AsString"):
                fn = getattr(entry, name, None)
                if callable(fn):
                    return fn()
            return getattr(entry, "StringValue", None)
        if storage_type == DB.StorageType.Integer:
            for name in ("GetIntegerValue", "AsInteger"):
                fn = getattr(entry, name, None)
                if callable(fn):
                    return fn()
            return getattr(entry, "IntegerValue", None)
        if storage_type == DB.StorageType.Double:
            for name in ("GetDoubleValue", "AsDouble"):
                fn = getattr(entry, name, None)
                if callable(fn):
                    return fn()
            return getattr(entry, "DoubleValue", None)
        if storage_type == DB.StorageType.ElementId:
            for name in ("GetElementIdValue", "AsElementId"):
                fn = getattr(entry, name, None)
                if callable(fn):
                    return fn()
            return getattr(entry, "ElementIdValue", None)
    except Exception:
        return None
    return None


def _entry_set_value(entry, storage_type, value):
    try:
        if storage_type == DB.StorageType.String:
            for name in ("SetStringValue",):
                fn = getattr(entry, name, None)
                if callable(fn):
                    fn("" if value is None else str(value))
                    return True
            if hasattr(entry, "StringValue"):
                entry.StringValue = "" if value is None else str(value)
                return True
        if storage_type == DB.StorageType.Integer:
            for name in ("SetIntegerValue",):
                fn = getattr(entry, name, None)
                if callable(fn):
                    fn(int(value if value is not None else 0))
                    return True
            if hasattr(entry, "IntegerValue"):
                entry.Value = int(value if value is not None else 0)
                return True
        if storage_type == DB.StorageType.Double:
            for name in ("SetDoubleValue",):
                fn = getattr(entry, name, None)
                if callable(fn):
                    fn(float(value if value is not None else 0.0))
                    return True
            if hasattr(entry, "DoubleValue"):
                entry.DoubleValue = float(value if value is not None else 0.0)
                return True
        if storage_type == DB.StorageType.ElementId:
            if not isinstance(value, DB.ElementId):
                value = DB.ElementId.InvalidElementId
            for name in ("SetElementIdValue",):
                fn = getattr(entry, name, None)
                if callable(fn):
                    fn(value)
                    return True
            if hasattr(entry, "ElementIdValue"):
                entry.ElementIdValue = value
                return True
    except Exception:
        return False
    return False


def _entry_caption(entry):
    try:
        getter = getattr(entry, "GetCaption", None)
        if callable(getter):
            val = getter()
            if val:
                return str(val)
    except Exception:
        pass
    try:
        val = getattr(entry, "Caption", None)
        if val:
            return str(val)
    except Exception:
        pass
    return ""


def _storage_type_name(storage_type):
    try:
        return str(storage_type)
    except Exception:
        return "<unknown>"


def _format_entry_value(entry):
    try:
        storage_type = getattr(entry, "StorageType", None)
        value = _entry_get_value(entry, storage_type)
        if storage_type == DB.StorageType.ElementId:
            return "ElementId({})".format(_elem_id_int(value))
        if storage_type == DB.StorageType.Double:
            return "{:.6f}".format(float(value))
        if value is None:
            return "<None>"
        return str(value)
    except Exception:
        return "<unreadable>"


def _format_visual_signature(sig):
    if sig is None:
        return "visuals=<unreadable>"
    rgb, fill_pattern_id, visible = sig
    return "rgb={}, fillPatternId={}, visible={}".format(rgb, fill_pattern_id, visible)


def _describe_entry(entry, index=None):
    prefix = "entry"
    if index is not None:
        prefix = "entry[{}]".format(index)
    return "{} caption='{}' storage={} value={} {}".format(
        prefix,
        _entry_caption(entry),
        _storage_type_name(getattr(entry, "StorageType", None)),
        _format_entry_value(entry),
        _format_visual_signature(_entry_visual_signature(entry)),
    )


def _describe_scheme(scheme):
    return "scheme='{}' id={} categoryId={} areaSchemeId={}".format(
        getattr(scheme, "Name", "<unnamed>"),
        _elem_id_int(getattr(scheme, "Id", None)),
        _elem_id_int(getattr(scheme, "CategoryId", None)),
        _elem_id_int(_scheme_area_scheme_id(scheme)),
    )


def _build_custom_entry_from_source(source_entry):
    try:
        storage_type = getattr(source_entry, "StorageType", None)
        if storage_type is None:
            return None
        custom = DB.ColorFillSchemeEntry(storage_type)
    except Exception:
        return None

    value = _entry_get_value(source_entry, storage_type)
    if not _entry_set_value(custom, storage_type, value):
        return None

    _copy_entry_visuals(source_entry, custom)
    return custom


def _clone_revit_color(color_obj):
    try:
        if color_obj is None:
            return None
        r = int(getattr(color_obj, "Red"))
        g = int(getattr(color_obj, "Green"))
        b = int(getattr(color_obj, "Blue"))
        return DB.Color(r, g, b)
    except Exception:
        return color_obj


def _copy_entry_visuals(source_entry, target_entry):
    # Some Revit versions are picky about mutating the Color object directly.
    if hasattr(source_entry, "Color") and hasattr(target_entry, "Color"):
        try:
            target_entry.Color = _clone_revit_color(getattr(source_entry, "Color"))
        except Exception:
            pass

    for attr in ("FillPatternId", "IsVisible", "Visible"):
        if hasattr(source_entry, attr) and hasattr(target_entry, attr):
            try:
                setattr(target_entry, attr, getattr(source_entry, attr))
            except Exception:
                pass

    caption = _entry_caption(source_entry)
    if caption:
        try:
            setter = getattr(target_entry, "SetCaption", None)
            if callable(setter):
                setter(caption)
            elif hasattr(target_entry, "Caption"):
                target_entry.Caption = caption
        except Exception:
            pass


def _patch_entry_colors(target_entries, source_entries, log=None, stage="patch"):
    """Re-apply source visuals onto target entries.

    Called after SetEntries to fix visuals that Revit may auto-reset for entries that
    are currently "In Use".

    Match order:
      1) caption (case-insensitive)
      2) entry value (storage-type aware)
      3) index fallback (when counts align)
    """
    src_by_cap = {}
    for se in source_entries:
        cap = _entry_caption(se).strip().lower()
        if cap:
            src_by_cap[cap] = se

    unmatched_targets = []
    matched_count = 0
    mismatch_count = 0
    for target_index, te in enumerate(target_entries):
        cap = _entry_caption(te).strip().lower()
        se = src_by_cap.get(cap)
        match_reason = None
        if se is None:
            for source_index, _se in enumerate(source_entries):
                if _same_entry_value(_se, te):
                    se = _se
                    match_reason = "value"
                    break
        else:
            match_reason = "caption"
        if se is None:
            unmatched_targets.append((target_index, te))
            _log(log, "[{}] skipped target {} reason=no source match".format(stage, _describe_entry(te, target_index)))
            continue
        matched_count += 1
        _copy_entry_visuals(se, te)
        if not _visuals_match_enough(se, te):
            mismatch_count += 1
            _log(log, "[{}] visual mismatch target {} matchedBy={}".format(stage, _describe_entry(te, target_index), match_reason or "unknown"))

    # Index fallback: common case for range schemes where captions may be empty and
    # values can be regenerated; if counts match, treat index as authoritative.
    try:
        if unmatched_targets and len(target_entries) == len(source_entries) and matched_count == 0:
            for idx, te in unmatched_targets:
                try:
                    _copy_entry_visuals(source_entries[idx], te)
                except Exception:
                    _log(log, "[{}] index fallback failed for target {}".format(stage, _describe_entry(te, idx)))
                    continue
        elif unmatched_targets:
            _log(
                log,
                "[{}] index fallback skipped reason=partial value/caption matching already succeeded".format(stage),
            )
    except Exception:
        pass
    _log(log, "[{}] summary matched={} unmatched={} mismatched={}".format(stage, matched_count, len(unmatched_targets), mismatch_count))


def _entry_visual_signature(entry):
    """Extract a minimal signature to compare if visuals match."""
    try:
        color = getattr(entry, "Color", None)
        rgb = None
        if color is not None:
            try:
                rgb = (int(color.Red), int(color.Green), int(color.Blue))
            except Exception:
                rgb = None
        fp = getattr(entry, "FillPatternId", None)
        fp_i = None
        try:
            fp_i = int(_elem_id_int(fp)) if fp is not None else None
        except Exception:
            fp_i = None
        vis = None
        for attr in ("IsVisible", "Visible"):
            if hasattr(entry, attr):
                try:
                    vis = bool(getattr(entry, attr))
                    break
                except Exception:
                    continue
        return (rgb, fp_i, vis)
    except Exception:
        return None


def _visuals_match_enough(source_entry, target_entry):
    try:
        return _entry_visual_signature(source_entry) == _entry_visual_signature(target_entry)
    except Exception:
        return False


def _regenerate_scheme_document(scheme):
    """Best-effort Document.Regenerate to let Revit settle scheme entry state."""
    try:
        doc = getattr(scheme, "Document", None)
        if doc is not None:
            doc.Regenerate()
    except Exception:
        pass


def _same_entry_value(a, b):
    try:
        st_a = getattr(a, "StorageType", None)
        st_b = getattr(b, "StorageType", None)
        if st_a != st_b:
            return False
        va = _entry_get_value(a, st_a)
        vb = _entry_get_value(b, st_b)
        if st_a == DB.StorageType.ElementId:
            try:
                ai = int(_elem_id_int(va))
                bi = int(_elem_id_int(vb))
                return ai == bi
            except Exception:
                return False
        if st_a == DB.StorageType.Double:
            try:
                fa = float(va)
                fb = float(vb)
                return abs(fa - fb) <= 1e-8
            except Exception:
                return False
        if st_a == DB.StorageType.String:
            try:
                return ("" if va is None else str(va)).strip() == ("" if vb is None else str(vb)).strip()
            except Exception:
                return False
        return va == vb
    except Exception:
        return False


def _overwrite_existing_matching_entry(target, source_entry, log=None, source_index=None):
    try:
        entries = list(target.GetEntries())
    except Exception:
        entries = []
    src_caption = _entry_caption(source_entry).strip().lower()
    for target_index, existing in enumerate(entries):
        try:
            same = _same_entry_value(existing, source_entry)
            match_reason = "value" if same else None
            if not same and src_caption:
                ex_caption = _entry_caption(existing).strip().lower()
                same = ex_caption and ex_caption == src_caption
                if same:
                    match_reason = "caption"
            if not same:
                continue
            _copy_entry_visuals(source_entry, existing)
            if not _visuals_match_enough(source_entry, existing):
                _log(log, "[fallback-overwrite] visual mismatch target {} matchedBy={}".format(_describe_entry(existing, target_index), match_reason or "unknown"))
            return True
        except Exception:
            continue
    _log(log, "[fallback-overwrite] skipped source {} reason=no matching target entry".format(_describe_entry(source_entry, source_index)))
    return False


def copy_scheme_data(source, target, log=None):
    _log(log, "[copy] start {} -> {}".format(_describe_scheme(source), _describe_scheme(target)))
    for attr in ("Title", "IsByRange", "IsByValue", "IsByPercentage"):
        if hasattr(source, attr) and hasattr(target, attr):
            try:
                setattr(target, attr, getattr(source, attr))
            except Exception:
                _log(log, "[copy] skipped scheme attr {} reason=setattr-failed".format(attr))
                pass
    for attr in ("ParameterDefinition", "ParameterId"):
        if hasattr(source, attr) and hasattr(target, attr):
            try:
                setattr(target, attr, getattr(source, attr))
            except Exception:
                _log(log, "[copy] skipped scheme attr {} reason=setattr-failed".format(attr))
                pass

    try:
        source_entries = list(source.GetEntries())
    except Exception:
        return False, "Unable to read entries from source scheme."
    _log(log, "[copy] source entries count={}".format(len(source_entries)))

    # Clone each entry so the target scheme receives fresh, independent objects.
    # Passing source entries directly can cause Revit to regenerate "in use" colors.
    entries_to_set = []
    for idx, _e in enumerate(source_entries):
        _clone_fn = getattr(_e, "Clone", None)
        if callable(_clone_fn):
            try:
                entries_to_set.append(_clone_fn())
                continue
            except Exception:
                pass
        _custom = _build_custom_entry_from_source(_e)
        entries_to_set.append(_custom if _custom is not None else _e)

    set_entries_applied = False
    try:
        set_entries = getattr(target, "SetEntries", None)
        if callable(set_entries):
            _log(log, "[copy] applying SetEntries targetCountBefore={}".format(len(list(target.GetEntries()))))
            set_entries(_to_entry_collection(entries_to_set))
            _log(log, "[copy] SetEntries applied candidateCount={}".format(len(entries_to_set)))

            # Revit can auto-reset colors for entries that are currently "In Use" in the
            # model shortly after SetEntries. Regenerate, then patch the now-live target
            # entries in-place using source as truth.
            _regenerate_scheme_document(target)
            _log(log, "[copy] regenerated document after SetEntries")
            try:
                refreshed = list(target.GetEntries())
                _log(log, "[copy] target entries after SetEntries count={}".format(len(refreshed)))
                if refreshed:
                    _patch_entry_colors(refreshed, source_entries, log=log, stage="post-set")
                    _regenerate_scheme_document(target)
                    _log(log, "[copy] regenerated document after post-set patch")
            except Exception:
                _log(log, "[copy] skipped post-set patch reason=exception")
                pass
            try:
                final_entries = list(target.GetEntries())
                _log(log, "[copy] target entries final count after SetEntries path={}".format(len(final_entries)))
                if len(final_entries) == len(entries_to_set):
                    set_entries_applied = True
                    _log(log, "[copy] SetEntries path succeeded")
                    return True, None
            except Exception:
                set_entries_applied = True
                _log(log, "[copy] SetEntries path succeeded; target entry recount unavailable")
                return True, None
        else:
            _log(log, "[copy] SetEntries unavailable on target")
    except Exception as ex:
        _log(log, "[copy] SetEntries failed; falling back to overwrite/add path reason={}".format(str(ex)))
        pass

    add_entry = getattr(target, "AddEntry", None)
    if not callable(add_entry):
        return False, "Target scheme API does not support SetEntries/AddEntry."

    added = 0
    overwritten = 0
    failures = []
    # Fallback: try to overwrite existing entries first (works better when entries
    # are "in use"), then add missing ones.
    for idx, entry in enumerate(source_entries):
        try:
            if _overwrite_existing_matching_entry(target, entry, log=log, source_index=idx):
                added += 1
                overwritten += 1
                continue
        except Exception:
            _log(log, "[fallback-overwrite] exception while matching source entry[{}]".format(idx))
            pass

        try:
            clone = getattr(entry, "Clone", None)
            new_entry = clone() if callable(clone) else entry
            add_entry(new_entry)
            added += 1
            continue
        except Exception as ex_add:
            try:
                custom_entry = _build_custom_entry_from_source(entry)
                if custom_entry is not None:
                    add_entry(custom_entry)
                    added += 1
                    continue
            except Exception as ex:
                if len(failures) < 5:
                    failures.append(str(ex))
            if len(failures) < 5:
                failures.append(str(ex_add) or "failed to add entry")
            _log(log, "[fallback-add] skipped source {} reason={}".format(_describe_entry(entry, idx), str(ex_add) or "failed to add entry"))

    _regenerate_scheme_document(target)
    _log(log, "[fallback] regenerated document after overwrite/add path")

    if added < len(source_entries):
        detail = (" Errors: " + " | ".join(failures)) if failures else ""
        _log(log, "[fallback] incomplete overwritten={} addedNew={} sourceCount={}".format(overwritten, added - overwritten, len(source_entries)))
        return False, "Only copied {} of {} entries.{}".format(added, len(source_entries), detail)
    _log(log, "[fallback] completed overwritten={} addedNew={} sourceCount={}".format(overwritten, added - overwritten, len(source_entries)))
    return True, None


def force_overwrite_scheme_visuals(source, target, log=None):
    """Force overwrite entry visuals on the target scheme.

    Some Revit versions can reset/randomize colors for schemes that are currently
    in use, especially at transaction commit. Calling this again in a follow-up
    transaction (after the initial copy) increases reliability.
    """
    try:
        source_entries = list(source.GetEntries())
    except Exception:
        return False, "Unable to read entries from source scheme."
    _log(log, "[finalize] start {} -> {}".format(_describe_scheme(source), _describe_scheme(target)))
    _log(log, "[finalize] source entries count={}".format(len(source_entries)))

    set_entries = getattr(target, "SetEntries", None)
    if not callable(set_entries):
        return False, "Target scheme API does not support SetEntries."

    try:
        _regenerate_scheme_document(target)
        _log(log, "[finalize] regenerated document before overwrite")
        current = list(target.GetEntries())
        if not current:
            return False, "Target scheme has no entries to overwrite."
        _log(log, "[finalize] target entries before overwrite count={}".format(len(current)))

        _patch_entry_colors(current, source_entries, log=log, stage="finalize")
        set_entries(_to_entry_collection(current))
        _log(log, "[finalize] SetEntries re-applied with patched target entries")
        _regenerate_scheme_document(target)
        refreshed = list(target.GetEntries())
        _log(log, "[finalize] target entries after overwrite count={}".format(len(refreshed)))
        return True, None
    except Exception as ex:
        _log(log, "[finalize] failed reason={}".format(str(ex)))
        return False, str(ex)


def _get_view_color_fill_scheme_id(view, category_id):
    try:
        method = getattr(view, "GetColorFillSchemeId", None)
        if callable(method):
            return method(category_id)
    except Exception:
        pass
    return None


def _set_view_color_fill_scheme_id(view, category_id, scheme_id):
    try:
        method = getattr(view, "SetColorFillSchemeId", None)
        if callable(method):
            method(category_id, scheme_id)
            return True
    except Exception:
        pass
    return False


def _list_target_area_color_schemes(doc, target_area_scheme_id):
    area_category_id = _to_element_id(DB.BuiltInCategory.OST_Areas)
    matches = []
    for scheme in _collect_color_fill_schemes(doc):
        try:
            if _elem_id_int(_scheme_area_scheme_id(scheme)) != _elem_id_int(target_area_scheme_id):
                continue
            if _elem_id_int(getattr(scheme, "CategoryId", None)) != _elem_id_int(area_category_id):
                continue
            matches.append(scheme)
        except Exception:
            continue
    return matches


def copy_view_area_color_scheme_with_scope(doc, source_view, target_view, target_area_scheme_id):
    area_category_id = _to_element_id(DB.BuiltInCategory.OST_Areas)
    source_scheme_id = _get_view_color_fill_scheme_id(source_view, area_category_id)
    if source_scheme_id is None or source_scheme_id == DB.ElementId.InvalidElementId:
        return False, "source view has no area color scheme assignment"
    source_scheme = doc.GetElement(source_scheme_id)
    if source_scheme is None:
        return False, "source color scheme element not found"

    # Scope seed: current target view scheme if valid in target area scheme, else first scheme in that scope.
    scope_seed = None
    target_current_id = _get_view_color_fill_scheme_id(target_view, area_category_id)
    if target_current_id is not None and target_current_id != DB.ElementId.InvalidElementId:
        try:
            current_target = doc.GetElement(target_current_id)
            if current_target is not None and _elem_id_int(_scheme_area_scheme_id(current_target)) == _elem_id_int(target_area_scheme_id):
                scope_seed = current_target
        except Exception:
            pass
    if scope_seed is None:
        candidates = _list_target_area_color_schemes(doc, target_area_scheme_id)
        if not candidates:
            return False, "target area scheme has no area color scheme to duplicate from"
        scope_seed = candidates[0]

    source_name = (getattr(source_scheme, "Name", "") or "Color Scheme").strip()
    selected_name = (getattr(scope_seed, "Name", "") or "").strip()
    target_scheme = scope_seed
    if selected_name.lower() != source_name.lower():
        all_schemes = _collect_color_fill_schemes(doc)
        existing_same_name = _find_scheme_in_scope_by_name(all_schemes, scope_seed, source_name)
        if existing_same_name is not None:
            target_scheme = existing_same_name
        else:
            try:
                new_id = scope_seed.Duplicate(source_name)
                created = doc.GetElement(new_id)
            except Exception as ex:
                created = None
                err = str(ex)
            if created is None:
                return False, "failed to create new scheme '{}' in target scope{}".format(
                    source_name,
                    (" ({})".format(err) if 'err' in locals() and err else ""),
                )
            target_scheme = created

    ok, msg = copy_scheme_data(source_scheme, target_scheme)
    if not ok:
        return False, msg
    if not _set_view_color_fill_scheme_id(target_view, area_category_id, target_scheme.Id):
        return False, "failed to assign target color scheme to target view ({})".format(
            getattr(target_scheme, "Name", "<unknown>")
        )
    return True, ""
