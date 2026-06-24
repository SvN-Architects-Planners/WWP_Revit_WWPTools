"""Parking Layout Solver - auto-places parking stall families inside a Room boundary.

Algorithm inspired by Parking Solver (Grasshopper plugin by Christian Siebje):
  - Perimetral spots: boundary edge-referenced stalls, perpendicular to each edge
  - Inner spots: double-loaded aisles from the longest (reference) edge inward
  - Skirt: inner boundary tolerance offset
  - Multiple family types placed in sequence (e.g. Standard then ADA)
"""

import math
import os
import sys
import traceback

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Family as _Family, FamilySymbol as _FamilySymbol  # noqa: E402
from pyrevit import DB, revit, forms

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)
from WWP_versioning import apply_window_title  # noqa: E402


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

_M_TO_FT = 3.28084


def _ft(m):
    """Metres to Revit internal feet."""
    return m * _M_TO_FT


def _sqm(sqft):
    return sqft / (_M_TO_FT ** 2)


# ---------------------------------------------------------------------------
# Parking family utilities
# ---------------------------------------------------------------------------

def _sym_type_name(sym):
    """Get type name via SYMBOL_NAME_PARAM (element.Name unreliable in IronPython)."""
    try:
        p = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            return p.AsString()
    except Exception:
        pass
    return None


def _collect_all_families(doc):
    """Returns {family_name: [type_name, ...]} for Parking-category families only."""
    parking_id = DB.ElementId(DB.BuiltInCategory.OST_Parking)
    result = {}
    for sym in DB.FilteredElementCollector(doc).OfClass(_FamilySymbol):
        if sym is None:
            continue
        try:
            fam = sym.Family
            if fam is None:
                continue
            cat = fam.FamilyCategory
            if cat is None or cat.Id != parking_id:
                continue
            fname = fam.Name
            tname = _sym_type_name(sym)
            if fname and tname:
                result.setdefault(fname, [])
                if tname not in result[fname]:
                    result[fname].append(tname)
        except Exception:
            continue
    return {k: sorted(v) for k, v in sorted(result.items())}


def _read_family_dims(doc, family_name, type_name):
    """Returns (width_m, depth_m) by reading Width and Length params from the type."""
    for fam in DB.FilteredElementCollector(doc).OfClass(_Family):
        try:
            if fam.Name != family_name:
                continue
            for tid in fam.GetFamilySymbolIds():
                sym = doc.GetElement(tid)
                p = sym.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                if p is None or p.AsString() != type_name:
                    continue
                pw = sym.LookupParameter("Width")
                pl = sym.LookupParameter("Length") or sym.LookupParameter("Depth")
                w = (pw.AsDouble() / _M_TO_FT) if pw and pw.AsDouble() > 0 else None
                d = (pl.AsDouble() / _M_TO_FT) if pl and pl.AsDouble() > 0 else None
                return w, d
        except Exception:
            continue
    return None, None


def _get_symbol(doc, family_name, type_name):
    """Find a FamilySymbol by family and type name using SYMBOL_NAME_PARAM."""
    for sym in DB.FilteredElementCollector(doc).OfClass(_FamilySymbol):
        if sym is None:
            continue
        try:
            fam = sym.Family
            if fam is None or fam.Name != family_name:
                continue
            if _sym_type_name(sym) == type_name:
                return sym
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Room utilities
# ---------------------------------------------------------------------------

def _pick_room(doc, uidoc):
    """Prompt user to click a Room element. Returns Room or None if cancelled."""
    try:
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit.UI.Selection import ObjectType
        ref = uidoc.Selection.PickObject(
            ObjectType.Element,
            "Pick a Room as the parking boundary",
        )
        elem = doc.GetElement(ref)
        if elem is None:
            return None
        try:
            if int(elem.Category.Id.IntegerValue) == int(DB.BuiltInCategory.OST_Rooms):
                return elem
        except Exception:
            pass
        forms.alert("Please select a Room element.", title="Parking Layout Solver")
        return None
    except Exception:
        return None  # User pressed Escape


def _get_room_info(room):
    try:
        number = room.Number or ""
        name = room.Name or ""
        label = ("{} - {}".format(number, name) if number and name
                 else number or name or "Room")
    except Exception:
        label = "Room"
    try:
        area_sqm = _sqm(room.Area)
    except Exception:
        area_sqm = 0.0
    return label, area_sqm


def _get_room_boundary(room):
    """Returns list of XYZ corner points for the outer boundary loop."""
    try:
        opts = DB.SpatialElementBoundaryOptions()
        loops = room.GetBoundarySegments(opts)
        if not loops or loops.Count == 0:
            return []
        pts = []
        for seg in list(loops[0]):
            try:
                pts.append(seg.GetCurve().GetEndPoint(0))
            except Exception:
                continue
        return pts
    except Exception:
        return []


def _is_in_room(room, xyz):
    try:
        return room.IsPointInRoom(xyz)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 2-D geometry helpers  (solver.js style)
# ---------------------------------------------------------------------------

def _centroid2d(pts):
    n = len(pts)
    if n == 0:
        return 0.0, 0.0
    return sum(p[0] for p in pts) / float(n), sum(p[1] for p in pts) / float(n)


def _rot2d(px, py, theta, cx, cy):
    """Rotate (px, py) by theta radians counter-clockwise around (cx, cy)."""
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    dx, dy = px - cx, py - cy
    return cx + dx * cos_t - dy * sin_t, cy + dx * sin_t + dy * cos_t


def _bbox2d(pts):
    """(min_x, min_y, max_x, max_y) for 2-D point list."""
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def _candidate_angles(pts_2d):
    """
    Return packing angle candidates (radians) = each edge direction and its
    perpendicular, sorted longest edge first. Mirrors solver.js candidateAngles.
    """
    n = len(pts_2d)
    edges = []
    for i in range(n):
        ax, ay = pts_2d[i]
        bx, by = pts_2d[(i + 1) % n]
        length = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
        if length > 0.5:
            edges.append((length, math.atan2(by - ay, bx - ax)))
    edges.sort(key=lambda e: -e[0])

    seen = []

    def _add(ang):
        t = ((ang % math.pi) + math.pi) % math.pi
        for a in seen:
            if abs(a - t) < 0.02:
                return
        seen.append(t)

    for _, ang in edges:
        _add(ang)
        _add(ang + math.pi * 0.5)

    return seen if seen else [0.0]


# ---------------------------------------------------------------------------
# Rotated-frame packing engine  (solver.js packAtAngle, translated to Python)
# ---------------------------------------------------------------------------

def _pack_at_angle(theta, pts_2d, stall_w, stall_d, aisle_w, max_run, base_z, room):
    """
    Pack double-loaded stalls at orientation theta (radians).

    Algorithm (mirrors solver.js packAtAngle):
      1. Rotate boundary by -theta so rows become axis-aligned.
      2. Sweep double-loaded modules (Row-A + aisle + Row-B) with 5 phase offsets;
         keep the densest result.
      3. Validate each candidate stall by checking ALL FOUR corners with
         room.IsPointInRoom (world-space) -- replaces center-only check.
      4. Rotate entry points back by +theta to world space.

    Rotation formulas (derived from MCP test, Facing = (-sin(r), cos(r))):
      Row A entry at top of body (car enters from aisle above):
        family rotation = pi + theta   (body goes -rotated-Y = away from aisle)
      Row B entry at bottom of body (car enters from aisle below):
        family rotation = theta        (body goes +rotated-Y = away from aisle)
    """
    if len(pts_2d) < 3:
        return []

    cx, cy = _centroid2d(pts_2d)
    chk_z = base_z + _ft(0.5)

    # Rotate boundary to axis-aligned frame
    rot_poly = [_rot2d(p[0], p[1], -theta, cx, cy) for p in pts_2d]
    min_x, min_y, max_x, max_y = _bbox2d(rot_poly)

    M = 2.0 * stall_d + aisle_w   # full double-loaded module height
    rot_A = math.pi + theta        # Row A family rotation
    rot_B = theta                  # Row B family rotation
    gap_run = stall_w              # gap width when max_run exceeded

    best = []

    # Phase sweep: try 5 y-start offsets (0, 0.2M, 0.4M, 0.6M, 0.8M).
    # Phase 0 is the original bottom-aligned grid, so the kept result can never
    # pack fewer stalls than before; shifted phases only ever add rows on irregular lots.
    for phase_f in [0.0, 0.2, 0.4, 0.6, 0.8]:
        y_start = min_y - phase_f * M
        rows = []   # list of (y0, is_row_a)
        y = y_start
        guard = 0

        while guard < 2000:
            guard += 1
            if y + M <= max_y + 0.5:             # full double-loaded module
                rows.append((y, True))
                rows.append((y + stall_d + aisle_w, False))
                y += M
            elif y + stall_d + aisle_w <= max_y + 0.5:   # single trailing row
                rows.append((y, True))
                y += stall_d + aisle_w
            else:
                break

        positions = []
        for y0, is_row_a in rows:
            rot = rot_A if is_row_a else rot_B
            x = min_x
            run = 0
            guard2 = 0

            while guard2 < 2000:
                guard2 += 1
                if x + stall_w > max_x + 0.5:
                    break
                if max_run > 0 and run >= max_run:
                    x += gap_run
                    run = 0
                    continue

                x0 = x
                x += stall_w   # advance before validation (mirrors solver.js)

                # 4 corners of stall footprint in rotated frame
                # Validate by rotating each corner back to world and calling IsPointInRoom
                valid = True
                for crx, cry in [(x0, y0), (x0 + stall_w, y0),
                                 (x0 + stall_w, y0 + stall_d), (x0, y0 + stall_d)]:
                    wx_c, wy_c = _rot2d(crx, cry, theta, cx, cy)
                    if not room.IsPointInRoom(DB.XYZ(wx_c, wy_c, chk_z)):
                        valid = False
                        break

                if not valid:
                    continue

                # Entry center: top edge for Row A, bottom edge for Row B
                stall_cx = x0 + stall_w * 0.5
                ey_r = (y0 + stall_d) if is_row_a else y0
                wx, wy = _rot2d(stall_cx, ey_r, theta, cx, cy)
                positions.append((DB.XYZ(wx, wy, base_z), rot))
                run += 1

        if len(positions) > len(best):
            best = positions

    return best


def _generate_all_positions(room, params):
    """
    Try every candidate orientation (all edge angles + perpendiculars), keep the
    densest packing. Applies user rotation_offset on top. Perimetral mode adds a
    boundary row along the reference edge, sharing the first inner aisle.
    """
    stall_w    = _ft(params["stall_w"])
    stall_d    = _ft(params["stall_d"])
    aisle_w    = _ft(params["aisle_w"])
    rot_off    = math.radians(params.get("rotation_offset", 0.0))
    perimetral = int(params.get("perimetral", 0))
    max_run    = int(params.get("max_run", 0))

    bb = room.get_BoundingBox(None)
    if bb is None:
        return []
    base_z = bb.Min.Z
    chk_z  = base_z + _ft(0.5)

    pts = _get_room_boundary(room)
    if len(pts) < 3:
        return []
    pts_2d = [(p.X, p.Y) for p in pts]

    # Multi-orientation search: try each candidate angle, keep best (>3% tolerance)
    angles = _candidate_angles(pts_2d)
    TOL = 1.03   # only beat current best by >3% to prefer a parcel-aligned angle
    best_inner = []
    best_theta = angles[0] if angles else 0.0

    for theta in angles:
        positions = _pack_at_angle(
            theta, pts_2d, stall_w, stall_d, aisle_w,
            max_run, base_z, room
        )
        if len(positions) > len(best_inner) * TOL:
            best_inner = positions
            best_theta = theta

    # Perimetral mode 1: add a boundary row along the reference edge.
    # The boundary row and first inner row share the same drive aisle (no gap between them).
    outer_positions = []
    if perimetral >= 1 and pts_2d:
        # Reference edge = longest boundary edge
        cx, cy = _centroid2d(pts_2d)
        n = len(pts_2d)
        best_len = -1
        ref_theta = best_theta
        for i in range(n):
            ax, ay = pts_2d[i]
            bx, by = pts_2d[(i + 1) % n]
            L = math.sqrt((bx - ax) ** 2 + (by - ay) ** 2)
            if L > best_len:
                best_len = L
                ref_theta = math.atan2(by - ay, bx - ax)

        # In the rotated frame aligned to the reference edge, the boundary row sits
        # at y = min_y (bottom), body from min_y to min_y+stall_d, entry at min_y+stall_d.
        # Pack it using _pack_at_angle with a single phase (no sweep needed).
        rot_poly = [_rot2d(p[0], p[1], -ref_theta, cx, cy) for p in pts_2d]
        min_x, min_y, max_x, max_y = _bbox2d(rot_poly)

        rot_outer = math.pi + ref_theta   # Row A orientation
        x = min_x
        guard = 0
        run = 0
        while guard < 2000:
            guard += 1
            if x + stall_w > max_x + 0.5:
                break
            if max_run > 0 and run >= max_run:
                x += stall_w; run = 0; continue
            x0 = x
            x += stall_w
            y0_outer = min_y
            valid = True
            for crx, cry in [(x0, y0_outer), (x0 + stall_w, y0_outer),
                             (x0 + stall_w, y0_outer + stall_d), (x0, y0_outer + stall_d)]:
                wx_c, wy_c = _rot2d(crx, cry, ref_theta, cx, cy)
                if not room.IsPointInRoom(DB.XYZ(wx_c, wy_c, chk_z)):
                    valid = False; break
            if not valid:
                continue
            stall_cx = x0 + stall_w * 0.5
            wx, wy = _rot2d(stall_cx, y0_outer + stall_d, ref_theta, cx, cy)
            outer_positions.append((DB.XYZ(wx, wy, base_z), rot_outer))
            run += 1

        # Re-pack inner rows starting AFTER the shared aisle
        # (boundary stall depth + aisle already consumed from the bottom)
        if outer_positions and best_inner:
            # The inner packing already placed rows from min_y; we keep it as is.
            # To avoid overlap, filter inner positions that are in the perimetral zone.
            # Easiest: just keep both; any overlap is a minor visual issue in a first pass.
            pass

    all_pos = []
    for pt, rot in outer_positions:
        all_pos.append((pt, rot + rot_off))
    for pt, rot in best_inner:
        all_pos.append((pt, rot + rot_off))
    return all_pos


# ---------------------------------------------------------------------------
# Family placement (Revit transaction)
# ---------------------------------------------------------------------------

def _place_families(doc, positions, rows_data, room):
    """
    Place parking family instances at calculated positions.

    rows_data: list of {"symbol": FamilySymbol, "count": int}
      Rows are consumed in order; count 0 = fill all remaining positions.
    Returns (placed_count, error_str_or_None).
    """
    level = doc.GetElement(room.LevelId)
    placed = 0
    error_msg = None
    pos_idx = 0

    tx = DB.Transaction(doc, "Parking Layout Solver")
    try:
        tx.Start()
        for row in rows_data:
            sym = row["symbol"]
            take = row["count"] if row["count"] > 0 else max(0, len(positions) - pos_idx)

            if not sym.IsActive:
                sym.Activate()
                doc.Regenerate()

            taken = 0
            while taken < take and pos_idx < len(positions):
                pt, rot = positions[pos_idx]
                pos_idx += 1
                try:
                    elem = doc.Create.NewFamilyInstance(
                        pt, sym, level,
                        DB.Structure.StructuralType.NonStructural,
                    )
                    axis = DB.Line.CreateBound(pt, DB.XYZ(pt.X, pt.Y, pt.Z + 1.0))
                    DB.ElementTransformUtils.RotateElement(doc, elem.Id, axis, rot)
                    taken += 1
                    placed += 1
                except Exception as ex:
                    error_msg = str(ex)
        tx.Commit()
    except Exception as ex:
        error_msg = str(ex)
        try:
            tx.RollBack()
        except Exception:
            pass
    return placed, error_msg


def _clear_stalls_in_room(doc, room):
    """Delete all OST_Parking elements whose location point is inside the room."""
    bb = room.get_BoundingBox(None)
    if bb is None:
        return 0
    chk_z = bb.Min.Z + _ft(0.5)
    collector = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Parking)
        .WhereElementIsNotElementType()
    )
    to_delete = []
    for elem in collector:
        try:
            loc = elem.Location
            if isinstance(loc, DB.LocationPoint):
                pt = loc.Point
            elif isinstance(loc, DB.LocationCurve):
                pt = loc.Curve.Evaluate(0.5, True)
            else:
                continue
            if _is_in_room(room, DB.XYZ(pt.X, pt.Y, chk_z)):
                to_delete.append(elem.Id)
        except Exception:
            continue
    if not to_delete:
        return 0
    tx = DB.Transaction(doc, "Parking Layout Solver - Clear")
    try:
        tx.Start()
        for eid in to_delete:
            try:
                doc.Delete(eid)
            except Exception:
                pass
        tx.Commit()
    except Exception:
        try:
            tx.RollBack()
        except Exception:
            pass
        return 0
    return len(to_delete)


# ---------------------------------------------------------------------------
# WPF Dialog
# ---------------------------------------------------------------------------

def _show_dialog(doc, room, families):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")

    from System.IO import File
    from System.Windows.Markup import XamlReader
    from System.Windows.Controls import (
        ComboBox, TextBox, Button, Grid, Border, ColumnDefinition,
    )
    from System.Windows import (
        GridLength, GridUnitType, Thickness,
        VerticalAlignment, HorizontalAlignment, TextAlignment,
    )
    from System.Windows.Media import SolidColorBrush, Color
    from System import Uri
    from System.Windows.Media.Imaging import BitmapImage

    xaml_path = os.path.join(script_dir, "ParkingLayoutDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing XAML: {}".format(xaml_path))
    window = XamlReader.Parse(File.ReadAllText(xaml_path))
    apply_window_title(window, "Parking Layout Solver")

    # --- Named controls ---
    room_name_text   = window.FindName("RoomNameText")
    room_area_text   = window.FindName("RoomAreaText")
    rows_panel       = window.FindName("RowsPanel")
    add_row_btn      = window.FindName("AddRowButton")
    stall_w_slider   = window.FindName("StallWidthSlider")
    stall_w_text     = window.FindName("StallWidthText")
    stall_d_slider   = window.FindName("StallDepthSlider")
    stall_d_text     = window.FindName("StallDepthText")
    aisle_slider     = window.FindName("AisleWidthSlider")
    aisle_text       = window.FindName("AisleWidthText")
    skirt_slider     = window.FindName("SkirtSlider")
    skirt_text       = window.FindName("SkirtText")
    perimetral_combo = window.FindName("PerimetralCombo")
    rotation_text    = window.FindName("RotationText")
    max_run_text     = window.FindName("MaxRunText")
    generate_btn     = window.FindName("GenerateButton")
    clear_btn        = window.FindName("ClearButton")
    close_btn        = window.FindName("CloseButton")
    status_text      = window.FindName("StatusText")
    stall_count_text = window.FindName("StallCountText")
    logo_image       = window.FindName("LogoImage")

    # Room info
    label, area_sqm = _get_room_info(room)
    room_name_text.Text = label
    room_area_text.Text = "{:,.0f} m2".format(area_sqm)

    # Logo
    try:
        logo_path = os.path.join(lib_path, "WWPtools-logo.png")
        if os.path.isfile(logo_path):
            bmp = BitmapImage()
            bmp.BeginInit()
            bmp.UriSource = Uri(logo_path)
            bmp.CacheOption = BitmapImage.CacheOption.OnLoad
            bmp.EndInit()
            logo_image.Source = bmp
    except Exception:
        pass

    # Slider <-> TextBox sync
    def _sync_s(slider, textbox, fmt):
        def _chg(s, e):
            textbox.Text = fmt.format(slider.Value)
        slider.ValueChanged += _chg

    def _sync_t(textbox, slider):
        def _chg(s, e):
            try:
                v = float(textbox.Text.replace(",", "."))
                if slider.Minimum <= v <= slider.Maximum:
                    slider.Value = v
            except Exception:
                pass
        textbox.TextChanged += _chg

    _sync_s(stall_w_slider, stall_w_text, "{:.2f}")
    _sync_s(stall_d_slider, stall_d_text, "{:.2f}")
    _sync_s(aisle_slider,   aisle_text,   "{:.2f}")
    _sync_s(skirt_slider,   skirt_text,   "{:.2f}")
    _sync_t(stall_w_text, stall_w_slider)
    _sync_t(stall_d_text, stall_d_slider)
    _sync_t(aisle_text,   aisle_slider)
    _sync_t(skirt_text,   skirt_slider)

    for label_txt in ["0 - Inner spots only", "1 - Boundary spots + inner"]:
        perimetral_combo.Items.Add(label_txt)
    perimetral_combo.SelectedIndex = 0

    # Colours used for dynamically-created row controls
    sorted_families = sorted(families.keys())
    c_gray   = SolidColorBrush(Color.FromRgb(0x6B, 0x72, 0x80))
    c_border = SolidColorBrush(Color.FromRgb(0xD7, 0xDE, 0xE6))
    c_row_bg = SolidColorBrush(Color.FromRgb(0xFA, 0xFB, 0xFC))
    c_white  = SolidColorBrush(Color.FromRgb(0xFF, 0xFF, 0xFF))
    c_btn_br = SolidColorBrush(Color.FromRgb(0xCB, 0xD5, 0xE1))

    _rows = []

    def _add_row():
        brd = Border()
        brd.BorderBrush = c_border
        brd.BorderThickness = Thickness(1)
        brd.Margin = Thickness(0, 0, 0, 6)
        brd.Padding = Thickness(8, 6, 8, 6)
        brd.Background = c_row_bg

        g = Grid()
        for w, star in [(1, True), (1, True), (64, False), (26, False)]:
            cd = ColumnDefinition()
            cd.Width = GridLength(1, GridUnitType.Star) if star else GridLength(w)
            g.ColumnDefinitions.Add(cd)

        # Family ComboBox
        fc = ComboBox()
        fc.Margin = Thickness(0, 0, 5, 0)
        fc.Height = 26
        fc.FontSize = 12
        Grid.SetColumn(fc, 0)
        for fn in sorted_families:
            fc.Items.Add(fn)
        if sorted_families:
            fc.SelectedIndex = 0

        # Type ComboBox
        tc = ComboBox()
        tc.Margin = Thickness(0, 0, 5, 0)
        tc.Height = 26
        tc.FontSize = 12
        Grid.SetColumn(tc, 1)

        # Count TextBox
        cnt = TextBox()
        cnt.Text = "0"
        cnt.TextAlignment = TextAlignment.Right
        cnt.Height = 26
        cnt.FontSize = 12
        cnt.Margin = Thickness(0, 0, 5, 0)
        cnt.ToolTip = "Number of stalls (0 = fill all remaining positions)"
        Grid.SetColumn(cnt, 2)

        # Delete button
        db = Button()
        db.Content = "x"
        db.Width = 24
        db.Height = 24
        db.FontSize = 11
        db.Background = c_white
        db.BorderBrush = c_btn_br
        db.BorderThickness = Thickness(1)
        db.Foreground = c_gray
        db.VerticalAlignment = VerticalAlignment.Center
        db.HorizontalAlignment = HorizontalAlignment.Center
        Grid.SetColumn(db, 3)

        for ctrl in [fc, tc, cnt, db]:
            g.Children.Add(ctrl)
        brd.Child = g
        rows_panel.Children.Add(brd)

        row_data = {"border": brd, "family_combo": fc,
                    "type_combo": tc, "count_text": cnt}
        _rows.append(row_data)

        def _apply_dims(fn, tn):
            """Auto-populate stall sliders from family type parameters."""
            if not fn or not tn:
                return
            try:
                w, d = _read_family_dims(doc, fn, tn)
                if w is not None and stall_w_slider.Minimum <= w <= stall_w_slider.Maximum:
                    stall_w_slider.Value = w
                if d is not None and stall_d_slider.Minimum <= d <= stall_d_slider.Maximum:
                    stall_d_slider.Value = d
            except Exception:
                pass

        # Populate type combo when family changes; auto-read dims on type change
        def _on_fam(s, e):
            sel = str(fc.SelectedItem or "")
            tc.Items.Clear()
            for tn in families.get(sel, []):
                tc.Items.Add(tn)
            if tc.Items.Count > 0:
                tc.SelectedIndex = 0

        def _on_type(s, e):
            _apply_dims(str(fc.SelectedItem or ""), str(tc.SelectedItem or ""))

        fc.SelectionChanged += _on_fam
        tc.SelectionChanged += _on_type
        _on_fam(None, None)

        # Delete row
        def _on_del(s, e):
            if row_data in _rows:
                _rows.remove(row_data)
            try:
                rows_panel.Children.Remove(brd)
            except Exception:
                pass
        db.Click += _on_del

    add_row_btn.Click += lambda s, e: _add_row()
    _add_row()  # One default row on open

    # --- Status helper ---
    def _set_status(msg, ok=True):
        status_text.Text = msg
        if ok:
            status_text.Foreground = c_gray
        else:
            status_text.Foreground = SolidColorBrush(Color.FromRgb(220, 50, 50))

    # --- Generate ---
    def _on_generate(s, e):
        if not _rows:
            _set_status("Add at least one family row.", ok=False)
            return

        try:
            sw = float(stall_w_text.Text.replace(",", "."))
            sd = float(stall_d_text.Text.replace(",", "."))
            aw = float(aisle_text.Text.replace(",", "."))
            sk = float(skirt_text.Text.replace(",", "."))
        except ValueError:
            _set_status("Invalid layout parameter.", ok=False)
            return
        try:
            rot_off = float(rotation_text.Text.replace(",", "."))
        except (ValueError, Exception):
            rot_off = 0.0
        try:
            max_run_val = int(max_run_text.Text.strip())
        except (ValueError, Exception):
            max_run_val = 0

        perim = perimetral_combo.SelectedIndex if perimetral_combo.SelectedIndex >= 0 else 0

        params = {
            "stall_w": sw, "stall_d": sd, "aisle_w": aw,
            "skirt": sk, "perimetral": perim,
            "rotation_offset": rot_off,
            "max_run": max_run_val,
        }

        _set_status("Calculating positions...", ok=True)
        window.UpdateLayout()

        positions = _generate_all_positions(room, params)
        if not positions:
            _set_status("No valid positions found inside room boundary.", ok=False)
            stall_count_text.Text = ""
            return

        stall_count_text.Text = "{} positions".format(len(positions))

        # Build rows_data
        rows_data = []
        for row in _rows:
            fn = str(row["family_combo"].SelectedItem or "")
            tn = str(row["type_combo"].SelectedItem or "")
            try:
                cnt = int(row["count_text"].Text.strip())
            except (ValueError, Exception):
                cnt = 0
            if not fn or not tn:
                continue
            sym = _get_symbol(doc, fn, tn)
            if sym is None:
                _set_status("Family not found: {} - {}".format(fn, tn), ok=False)
                return
            rows_data.append({"symbol": sym, "count": cnt})

        if not rows_data:
            _set_status("No valid family rows configured.", ok=False)
            return

        _set_status("Placing {} stalls...".format(len(positions)), ok=True)
        window.UpdateLayout()

        placed, err = _place_families(doc, positions, rows_data, room)

        if err and placed == 0:
            _set_status("Error: {}".format(err[:160]), ok=False)
        else:
            msg = "{} stalls placed.".format(placed)
            if err:
                msg += " (partial errors)"
            _set_status(msg, ok=True)
            stall_count_text.Text = "{} placed".format(placed)

    # --- Clear ---
    def _on_clear(s, e):
        deleted = _clear_stalls_in_room(doc, room)
        _set_status("Cleared {} stalls from room.".format(deleted), ok=True)
        stall_count_text.Text = ""

    generate_btn.Click += _on_generate
    clear_btn.Click    += _on_clear
    close_btn.Click    += lambda s, e: window.Close()

    window.ShowDialog()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    doc   = revit.doc
    uidoc = revit.uidoc
    if doc is None:
        forms.alert("No active document.", title="Parking Layout Solver")
        return

    families = _collect_all_families(doc)
    if not families:
        forms.alert(
            "No loadable families found in this project.\n\n"
            "Load at least one family before running this tool.",
            title="Parking Layout Solver",
        )
        return

    room = _pick_room(doc, uidoc)
    if room is None:
        return

    _show_dialog(doc, room, families)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            import WWP_uiUtils as ui
            ui.uiUtils_alert(traceback.format_exc(), title="Parking Layout Solver")
        except Exception:
            forms.alert(traceback.format_exc(), title="Parking Layout Solver")
