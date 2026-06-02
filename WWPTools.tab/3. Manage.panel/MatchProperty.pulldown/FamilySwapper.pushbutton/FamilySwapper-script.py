import clr
clr.AddReference('System.Data')
import System
import System.Data as SD
from System.Windows.Controls import DataGridEditingUnit

from pyrevit import revit, DB, forms
import WWP_uiUtils as ui
import WWP_telemetry

doc = revit.doc


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------

def _is_swappable(param):
    """True for custom, writable, integer/string/double instance parameters."""
    if param.IsReadOnly:
        return False
    valid_types = (DB.StorageType.Integer, DB.StorageType.String, DB.StorageType.Double)
    if param.StorageType not in valid_types:
        return False
    # Built-in system parameters have negative element IDs
    if param.Id.IntegerValue < 0:
        return False
    return True


def _collect_param_names(tb_instance):
    return sorted(set(p.Definition.Name for p in tb_instance.Parameters if _is_swappable(p)))


def _suggest_target(src_name, tgt_set, tgt_lower):
    """Exact then case-insensitive name match; empty string if no match."""
    if src_name in tgt_set:
        return src_name
    candidate = tgt_lower.get(src_name.lower())
    return candidate or ''


def _safe_str(value):
    if value is None or value == System.DBNull.Value:
        return ''
    return str(value).strip()


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class FamilySwapperWindow(forms.WPFWindow):

    def __init__(self):
        forms.WPFWindow.__init__(self, 'FamilySwapper.xaml')
        self._setup_grids()
        self._wire_events()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_grids(self):
        self._type_dt = SD.DataTable('TypeMap')
        self._type_dt.Columns.Add('SourceType', System.String)
        self._type_dt.Columns.Add('TargetType', System.String)
        self.TypeGrid.ItemsSource = self._type_dt.DefaultView

        self._param_dt = SD.DataTable('ParamMap')
        self._param_dt.Columns.Add('OldName', System.String)
        self._param_dt.Columns.Add('NewName', System.String)
        self.ParamGrid.ItemsSource = self._param_dt.DefaultView

    def _wire_events(self):
        self.AddTypeRowBtn.Click += self._add_type_row_click
        self.RemTypeRowBtn.Click += self._rem_type_row_click
        self.AddParamRowBtn.Click += self._add_param_row_click
        self.RemParamRowBtn.Click += self._rem_param_row_click
        self.DiscoverBtn.Click += self._discover_click
        self.RunBtn.Click += self._run_click

    # ------------------------------------------------------------------
    # Type grid
    # ------------------------------------------------------------------

    def _add_type_row_click(self, sender, args):
        self._add_type_row()

    def _rem_type_row_click(self, sender, args):
        selected = self.TypeGrid.SelectedItem
        if selected is not None:
            selected.Row.Delete()
            self._type_dt.AcceptChanges()

    def _add_type_row(self, src='', tgt=''):
        row = self._type_dt.NewRow()
        row['SourceType'] = src
        row['TargetType'] = tgt
        self._type_dt.Rows.Add(row)

    # ------------------------------------------------------------------
    # Param grid
    # ------------------------------------------------------------------

    def _add_param_row_click(self, sender, args):
        self._add_param_row()

    def _rem_param_row_click(self, sender, args):
        selected = self.ParamGrid.SelectedItem
        if selected is not None:
            selected.Row.Delete()
            self._param_dt.AcceptChanges()

    def _add_param_row(self, old='', new=''):
        row = self._param_dt.NewRow()
        row['OldName'] = old
        row['NewName'] = new
        self._param_dt.Rows.Add(row)

    def _existing_src_params(self):
        return set(_safe_str(r['OldName']) for r in self._param_dt.Rows if _safe_str(r['OldName']))

    # ------------------------------------------------------------------
    # Discover
    # ------------------------------------------------------------------

    def _discover_click(self, sender, args):
        self._commit_grids()
        src_fam = self.SourceFamilyBox.Text.strip()
        if not src_fam:
            self._log('Enter source family name first.')
            return

        type_name_map = self._read_type_map()

        all_tb_types = (DB.FilteredElementCollector(doc)
                        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                        .WhereElementIsElementType()
                        .ToElements())
        src_type_ids, tgt_type_ids = set(), set()
        tgt_type_names = set(type_name_map.values())
        for t in all_tb_types:
            fam = getattr(t.Family, 'Name', '')
            p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            typ = p.AsString() if p else ''
            if fam == src_fam:
                src_type_ids.add(t.Id.IntegerValue)
            if typ in tgt_type_names:
                tgt_type_ids.add(t.Id.IntegerValue)

        all_tbs = (DB.FilteredElementCollector(doc)
                   .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                   .WhereElementIsNotElementType()
                   .ToElements())
        sample_src = next((tb for tb in all_tbs if tb.GetTypeId().IntegerValue in src_type_ids), None)
        sample_tgt = next((tb for tb in all_tbs if tb.GetTypeId().IntegerValue in tgt_type_ids), None)

        if not sample_src:
            self._log('No source titleblock instances found — check source family name.')
            return

        src_names = _collect_param_names(sample_src)
        tgt_names = _collect_param_names(sample_tgt) if sample_tgt else []
        tgt_set = set(tgt_names)
        tgt_lower = {n.lower(): n for n in tgt_names}

        existing = self._existing_src_params()
        added = 0
        for name in src_names:
            if name in existing:
                continue
            self._add_param_row(name, _suggest_target(name, tgt_set, tgt_lower))
            added += 1

        self._log('Discovered {} parameter(s) from source.'.format(added))
        if not sample_tgt:
            self._log('  (no target instance found — target suggestions unavailable)')

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def _run_click(self, sender, args):
        self.LogBox.Text = ''
        self.RunBtn.IsEnabled = False
        try:
            self._run()
        finally:
            self.RunBtn.IsEnabled = True

    def _commit_grids(self):
        self.TypeGrid.CommitEdit(DataGridEditingUnit.Row, True)
        self.ParamGrid.CommitEdit(DataGridEditingUnit.Row, True)

    def _read_type_map(self):
        result = {}
        for row in self._type_dt.Rows:
            src = _safe_str(row['SourceType'])
            tgt = _safe_str(row['TargetType'])
            if src and tgt:
                result[src] = tgt
        return result

    def _run(self):
        self._commit_grids()

        src_fam = self.SourceFamilyBox.Text.strip()
        if not src_fam:
            self._log('ERROR: Source family name is required.')
            return

        try:
            shift_x_mm = float(self.ShiftXBox.Text or '0')
            shift_y_mm = float(self.ShiftYBox.Text or '0')
            batch_size = int(self.BatchSizeBox.Text or '40')
        except Exception:
            self._log('ERROR: Shift and batch size values must be numbers.')
            return

        type_name_map = self._read_type_map()
        if not type_name_map:
            self._log('ERROR: Add at least one type mapping.')
            return

        param_map = {}
        for row in self._param_dt.Rows:
            old = _safe_str(row['OldName'])
            new = _safe_str(row['NewName'])
            if old:
                param_map[old] = new if new else old

        # Resolve type names to element IDs
        all_tb_types = (DB.FilteredElementCollector(doc)
                        .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                        .WhereElementIsElementType()
                        .ToElements())
        src_type_ids, tgt_type_ids = {}, {}
        tgt_type_names = set(type_name_map.values())
        for t in all_tb_types:
            fam = getattr(t.Family, 'Name', '')
            p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            typ = p.AsString() if p else ''
            if fam == src_fam:
                src_type_ids[typ] = t.Id.IntegerValue
            if typ in tgt_type_names:
                tgt_type_ids[typ] = t.Id.IntegerValue

        type_id_map = {}
        for src_typ, tgt_typ in type_name_map.items():
            if src_typ in src_type_ids and tgt_typ in tgt_type_ids:
                type_id_map[src_type_ids[src_typ]] = tgt_type_ids[tgt_typ]
            else:
                msg = "WARNING: could not resolve '{}' -> '{}'".format(src_typ, tgt_typ)
                if src_typ not in src_type_ids:
                    msg += "\n  '{}' not in family '{}'".format(src_typ, src_fam)
                if tgt_typ not in tgt_type_ids:
                    msg += "\n  Target type '{}' not found".format(tgt_typ)
                self._log(msg)

        if not type_id_map:
            self._log('ERROR: No type mappings resolved.')
            return

        src_id_set = set(type_id_map.keys())
        all_tbs = (DB.FilteredElementCollector(doc)
                   .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                   .WhereElementIsNotElementType()
                   .ToElements())
        remaining = [tb for tb in all_tbs if tb.GetTypeId().IntegerValue in src_id_set]
        batch = remaining[:batch_size]

        self._log('Source remaining : {}'.format(len(remaining)))
        self._log('Processing batch : {}'.format(len(batch)))
        if not batch:
            self._log('Nothing to process.')
            return

        shift_x_ft = shift_x_mm / 304.8
        shift_y_ft = shift_y_mm / 304.8
        do_shift = shift_x_mm != 0 or shift_y_mm != 0

        ok, errors = 0, []
        t = DB.Transaction(doc, 'Family Swapper')
        t.Start()
        for tb in batch:
            sheet = None
            try:
                old_type_id = tb.GetTypeId().IntegerValue
                sheet = doc.GetElement(tb.OwnerViewId)
                old_vals = {}
                for p in tb.Parameters:
                    if _is_swappable(p):
                        n = p.Definition.Name
                        if p.StorageType == DB.StorageType.Integer:
                            old_vals[n] = ('int', p.AsInteger())
                        elif p.StorageType == DB.StorageType.String:
                            old_vals[n] = ('str', p.AsString())
                        elif p.StorageType == DB.StorageType.Double:
                            old_vals[n] = ('dbl', p.AsDouble())
                was_pinned = tb.Pinned
                if was_pinned:
                    tb.Pinned = False
                tb.ChangeTypeId(DB.ElementId(type_id_map[old_type_id]))
                if do_shift:
                    DB.ElementTransformUtils.MoveElement(doc, tb.Id, DB.XYZ(shift_x_ft, shift_y_ft, 0))
                if was_pinned:
                    tb.Pinned = True
                for old_n, new_n in param_map.items():
                    if old_n not in old_vals:
                        continue
                    t_type, val = old_vals[old_n]
                    new_p = tb.LookupParameter(new_n)
                    if new_p is None or new_p.IsReadOnly:
                        continue
                    if t_type == 'int':
                        new_p.Set(val)
                    elif t_type == 'str':
                        new_p.Set(val)
                    elif t_type == 'dbl':
                        new_p.Set(val)
                ok += 1
            except Exception as ex:
                sn = sheet.SheetNumber if sheet else '?'
                errors.append('Sheet {} : {}'.format(sn, str(ex)[:100]))
        t.Commit()

        self._log('\nResult : {}/{} succeeded'.format(ok, len(batch)))
        left = len(remaining) - ok
        if left > 0:
            self._log('{} sheet(s) still pending — run again.'.format(left))
        else:
            self._log('All done!')
        if errors:
            self._log('\nErrors ({}) :'.format(len(errors)))
            for err in errors:
                self._log('  ' + err)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(self, msg):
        current = self.LogBox.Text
        self.LogBox.Text = (current + '\n' + msg).lstrip('\n')
        self.LogBox.ScrollToEnd()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    try:
        FamilySwapperWindow().ShowDialog()
        WWP_telemetry.track_use('FamilySwapper')
    except Exception:
        import traceback
        ui.uiUtils_alert(traceback.format_exc())
