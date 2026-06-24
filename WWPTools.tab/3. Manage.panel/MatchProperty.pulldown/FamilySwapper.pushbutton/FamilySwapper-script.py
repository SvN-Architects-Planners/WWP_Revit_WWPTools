import clr
clr.AddReference('System.Data')
clr.AddReference('PresentationFramework')
import System
import System.Data as SD
from System.Collections.Generic import List as NList
from System.Windows.Controls import DataGridEditingUnit, DataGridComboBoxColumn

from pyrevit import revit, DB, forms
import WWP_uiUtils as ui
import WWP_telemetry

doc = revit.doc


# ---------------------------------------------------------------------------
# Revit API compatibility
# ---------------------------------------------------------------------------

def _net_str_list(py_list):
    """Convert a Python string list to List[String] for WPF ItemsSource binding."""
    lst = NList[System.String]()
    for item in py_list:
        lst.Add(item)
    return lst


def _id_val(eid):
    """Return the integer value of an ElementId - handles Revit 2025+ (.Value) and older (.IntegerValue)."""
    try:
        return eid.Value
    except AttributeError:
        return eid.IntegerValue


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------

def _is_swappable(param):
    if param.IsReadOnly:
        return False
    valid_types = (DB.StorageType.Integer, DB.StorageType.String, DB.StorageType.Double)
    if param.StorageType not in valid_types:
        return False
    if _id_val(param.Id) < 0:
        return False
    return True


def _collect_param_names(tb_instance):
    names = set()
    for p in tb_instance.Parameters:
        if _is_swappable(p):
            names.add(p.Definition.Name)
    ftype = doc.GetElement(tb_instance.GetTypeId())
    if ftype:
        for p in ftype.Parameters:
            if _is_swappable(p):
                names.add(p.Definition.Name)
    return sorted(names)


def _suggest_target(src_name, tgt_set, tgt_lower):
    if src_name in tgt_set:
        return src_name
    return tgt_lower.get(src_name.lower(), '')


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
        self._family_type_map    = {}  # {family_name: [sorted type names]}
        self._family_cat_map     = {}  # {family_name: category ElementId}
        self._family_type_id_map = {}  # {family_name: {type_name: element_id}}
        self._src_cat_id = None
        self._src_types = []
        self._tgt_types = []
        self._src_params = []
        self._tgt_params = []
        self._setup_grids()
        self._wire_events()   # wire before load so SelectionChanged fires on initial selection
        self._load_families()

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

    def _load_families(self):
        all_tb_types = (DB.FilteredElementCollector(doc)
                        .OfClass(DB.FamilySymbol)
                        .ToElements())
        fam_types    = {}
        fam_cats     = {}
        fam_type_ids = {}  # {family_name: {type_name: element_id}}
        for t in all_tb_types:
            fam = t.Family.Name if t.Family else ''
            p = t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            typ = p.AsString() if p else ''
            if fam:
                if fam not in fam_types:
                    fam_types[fam]    = []
                    fam_type_ids[fam] = {}
                if typ:
                    if typ not in fam_types[fam]:
                        fam_types[fam].append(typ)
                    fam_type_ids[fam][typ] = _id_val(t.Id)
                if fam not in fam_cats and t.Category:
                    fam_cats[fam] = t.Category.Id
        for fam in fam_types:
            fam_types[fam].sort()
        self._family_type_map    = fam_types
        self._family_cat_map     = fam_cats
        self._family_type_id_map = fam_type_ids

        families = sorted(fam_types.keys())
        for fam in families:
            self.SourceFamilyCmb.Items.Add(fam)
            self.TargetFamilyCmb.Items.Add(fam)

        detected = self._detect_source_family()
        if detected and detected in fam_types:
            self.SourceFamilyCmb.SelectedItem = detected
        elif families:
            self.SourceFamilyCmb.SelectedIndex = 0

    def _detect_source_family(self):
        try:
            names = set()
            for eid in revit.uidoc.Selection.GetElementIds():
                elem = doc.GetElement(eid)
                if isinstance(elem, DB.FamilyInstance):
                    ftype = doc.GetElement(elem.GetTypeId())
                    if ftype and ftype.Family:
                        names.add(ftype.Family.Name)
            if len(names) == 1:
                return names.pop()
        except Exception:
            pass
        return None

    def _wire_events(self):
        self.SourceFamilyCmb.SelectionChanged += self._on_source_family_changed
        self.TargetFamilyCmb.SelectionChanged += self._on_target_family_changed
        self.SetTargetBtn.Click   += self._set_target_click
        self.AddTypeRowBtn.Click  += self._add_type_row_click
        self.RemTypeRowBtn.Click  += self._rem_type_row_click
        self.AddParamRowBtn.Click += self._add_param_row_click
        self.RemParamRowBtn.Click += self._rem_param_row_click
        self.DiscoverBtn.Click    += self._discover_click
        self.RunBtn.Click         += self._run_click

    # ------------------------------------------------------------------
    # Family selection handlers
    # ------------------------------------------------------------------

    def _on_source_family_changed(self, sender, args):
        fam = self.SourceFamilyCmb.SelectedItem
        self._src_types  = list(self._family_type_map.get(str(fam), [])) if fam else []
        self._src_cat_id = self._family_cat_map.get(str(fam)) if fam else None
        self.TypeGrid.Columns[0].ItemsSource = _net_str_list(self._src_types)
        self._rebuild_type_rows()

    def _on_target_family_changed(self, sender, args):
        fam = self.TargetFamilyCmb.SelectedItem
        self._tgt_types = list(self._family_type_map.get(str(fam), [])) if fam else []
        self.TypeGrid.Columns[1].ItemsSource = _net_str_list(self._tgt_types)
        # Refresh target suggestions in existing rows
        tgt_set   = set(self._tgt_types)
        tgt_lower = {t.lower(): t for t in self._tgt_types}
        for row in self._type_dt.Rows:
            src = _safe_str(row['SourceType'])
            if src:
                row['TargetType'] = _suggest_target(src, tgt_set, tgt_lower)
        self._type_dt.AcceptChanges()

    # ------------------------------------------------------------------
    # Type grid
    # ------------------------------------------------------------------

    def _rebuild_type_rows(self):
        self._type_dt.Clear()
        tgt_set   = set(self._tgt_types)
        tgt_lower = {t.lower(): t for t in self._tgt_types}
        for src in self._src_types:
            row = self._type_dt.NewRow()
            row['SourceType'] = src
            row['TargetType'] = _suggest_target(src, tgt_set, tgt_lower)
            self._type_dt.Rows.Add(row)

    def _add_type_row_click(self, sender, args):
        row = self._type_dt.NewRow()
        row['SourceType'] = ''
        row['TargetType'] = ''
        self._type_dt.Rows.Add(row)

    def _rem_type_row_click(self, sender, args):
        selected = self.TypeGrid.SelectedItem
        if selected is not None:
            selected.Row.Delete()
            self._type_dt.AcceptChanges()

    def _read_type_map(self):
        result = {}
        for row in self._type_dt.Rows:
            src = _safe_str(row['SourceType'])
            tgt = _safe_str(row['TargetType'])
            if src and tgt:
                result[src] = tgt
        return result

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
    # Set target / Discover
    # ------------------------------------------------------------------

    def _instances_of_family(self, fam_name):
        """Return placed instances of fam_name using the source family's category."""
        cat_id = self._src_cat_id
        if cat_id is None:
            return []
        all_types = (DB.FilteredElementCollector(doc)
                     .OfCategoryId(cat_id)
                     .WhereElementIsElementType()
                     .ToElements())
        type_ids = set()
        for t in all_types:
            fam = t.Family.Name if t.Family else ''
            if fam == fam_name:
                type_ids.add(_id_val(t.Id))
        all_instances = (DB.FilteredElementCollector(doc)
                         .OfCategoryId(cat_id)
                         .WhereElementIsNotElementType()
                         .ToElements())
        return [inst for inst in all_instances if _id_val(inst.GetTypeId()) in type_ids]

    def _set_target_click(self, sender, args):
        tgt_fam = str(self.TargetFamilyCmb.SelectedItem or '')
        if not tgt_fam:
            self._log('Select a target family first.')
            return

        cat_id = self._src_cat_id
        if cat_id is None:
            self._log('Select source family first so its category is known.')
            return

        names = set()

        # Read all parameters (instance and type) from the project parameter registry,
        # filtered to the source family's category (not hardcoded to OST_TitleBlocks).
        cat_id_val = _id_val(cat_id)
        it = doc.ParameterBindings.ForwardIterator()
        while it.MoveNext():
            for cat in it.Current.Categories:
                if _id_val(cat.Id) == cat_id_val:
                    names.add(it.Key.Name)
                    break

        tgt_names = sorted(names)
        self._tgt_params = tgt_names
        self.ParamGrid.Columns[1].ItemsSource = _net_str_list(self._tgt_params)
        tgt_set   = set(tgt_names)
        tgt_lower = {n.lower(): n for n in tgt_names}
        for row in self._param_dt.Rows:
            old = _safe_str(row['OldName'])
            if old:
                row['NewName'] = _suggest_target(old, tgt_set, tgt_lower)
        self._param_dt.AcceptChanges()
        self._log('Target set: {} parameter(s) available for "{}".'.format(len(tgt_names), tgt_fam))

    def _discover_click(self, sender, args):
        self._commit_grids()
        src_fam = str(self.SourceFamilyCmb.SelectedItem or '')
        if not src_fam:
            self._log('Select source family first.')
            return

        instances = self._instances_of_family(src_fam)
        if not instances:
            self._log('No placed instances of source family found.')
            return

        src_names = _collect_param_names(instances[0])
        tgt_set   = set(self._tgt_params)
        tgt_lower = {n.lower(): n for n in self._tgt_params}

        self._src_params = src_names
        self.ParamGrid.Columns[0].ItemsSource = _net_str_list(self._src_params)

        existing = self._existing_src_params()
        added = 0
        for name in src_names:
            if name in existing:
                continue
            self._add_param_row(name, _suggest_target(name, tgt_set, tgt_lower))
            added += 1

        self._log('Discovered {} parameter(s) from source.'.format(added))
        if not self._tgt_params:
            self._log('  (click Set target to populate target parameter suggestions)')

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

    def _run(self):
        self._commit_grids()

        src_fam = str(self.SourceFamilyCmb.SelectedItem or '')
        if not src_fam:
            self._log('ERROR: Select source family.')
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

        cat_id = self._src_cat_id
        if cat_id is None:
            self._log('ERROR: Source family category unknown. Re-select source family.')
            return
        tgt_fam = str(self.TargetFamilyCmb.SelectedItem or '')
        if not tgt_fam:
            self._log('ERROR: Select target family.')
            return

        tgt_type_names = set(type_name_map.values())
        src_type_ids   = dict(self._family_type_id_map.get(src_fam, {}))
        tgt_type_ids   = {n: i for n, i in self._family_type_id_map.get(tgt_fam, {}).items()
                          if n in tgt_type_names}

        type_id_map = {}
        for src_typ, tgt_typ in type_name_map.items():
            if src_typ in src_type_ids and tgt_typ in tgt_type_ids:
                type_id_map[src_type_ids[src_typ]] = tgt_type_ids[tgt_typ]
            else:
                msg = "WARNING: could not resolve '{}' -> '{}'".format(src_typ, tgt_typ)
                if src_typ not in src_type_ids:
                    msg += "\n  '{}' not found in family '{}'".format(src_typ, src_fam)
                if tgt_typ not in tgt_type_ids:
                    msg += "\n  Target type '{}' not found".format(tgt_typ)
                self._log(msg)

        if not type_id_map:
            self._log('ERROR: No type mappings resolved.')
            return

        src_id_set = set(type_id_map.keys())
        all_tbs = (DB.FilteredElementCollector(doc)
                   .OfCategoryId(cat_id)
                   .WhereElementIsNotElementType()
                   .ToElements())
        remaining = [tb for tb in all_tbs if _id_val(tb.GetTypeId()) in src_id_set]
        batch     = remaining[:batch_size]

        self._log('Source remaining : {}'.format(len(remaining)))
        self._log('Processing batch : {}'.format(len(batch)))
        if not batch:
            self._log('Nothing to process.')
            return

        shift_x_ft = shift_x_mm / 304.8
        shift_y_ft = shift_y_mm / 304.8
        do_shift   = shift_x_mm != 0 or shift_y_mm != 0

        ok, errors = 0, []
        type_param_skipped = set()
        t = DB.Transaction(doc, 'Family Swapper')
        t.Start()
        for tb in batch:
            try:
                old_type_id = _id_val(tb.GetTypeId())
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
                old_ftype = doc.GetElement(tb.GetTypeId())
                if old_ftype:
                    for p in old_ftype.Parameters:
                        if _is_swappable(p) and p.Definition.Name not in old_vals:
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
                new_ftype = doc.GetElement(tb.GetTypeId())
                for old_n, new_n in param_map.items():
                    if old_n not in old_vals:
                        continue
                    t_type, val = old_vals[old_n]
                    new_p = tb.LookupParameter(new_n)
                    if new_p is None or new_p.IsReadOnly:
                        # Skip type params -- writing them affects every instance of the
                        # new type, not just this one. Track for end-of-run notice.
                        if new_ftype and new_ftype.LookupParameter(new_n):
                            type_param_skipped.add(new_n)
                        continue
                    if t_type == 'int':
                        new_p.Set(val)
                    elif t_type == 'str':
                        new_p.Set(val)
                    elif t_type == 'dbl':
                        new_p.Set(val)
                ok += 1
            except Exception as ex:
                errors.append('Element {} : {}'.format(_id_val(tb.Id), str(ex)[:100]))
        t.Commit()

        self._log('\nResult : {}/{} succeeded'.format(ok, len(batch)))
        left = len(remaining) - ok
        if left > 0:
            self._log('{} instance(s) still pending - run again.'.format(left))
        else:
            self._log('All done!')
        if type_param_skipped:
            self._log('\nNote: {} type parameter(s) skipped (type params affect all instances, edit manually):'.format(len(type_param_skipped)))
            for name in sorted(type_param_skipped):
                self._log('  ' + name)
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
