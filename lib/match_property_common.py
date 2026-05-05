# -*- coding: utf-8 -*-
import os
import sys


# ---------------------------------------------------------------------------
# Revit helpers
# ---------------------------------------------------------------------------

def _elem_id_int(eid):
	try:
		return int(eid.Value)
	except AttributeError:
		return int(eid.IntegerValue)


def get_element_category_name(elem):
	if elem is not None and elem.Category is not None:
		return elem.Category.Name
	return "Unknown"


def get_element_category_id(elem):
	if elem is not None and elem.Category is not None:
		return elem.Category.Id
	return None


def get_element_display_name(elem, doc):
	if elem is None:
		return "Unknown"
	try:
		name = elem.Name
		if name:
			return str(name)
	except Exception:
		pass
	try:
		return "Element #{}".format(_elem_id_int(elem.Id))
	except Exception:
		return "Unknown"


def get_param_value_str(param):
	"""Return a human-readable string for a parameter value."""
	from pyrevit import DB
	if param is None:
		return ""
	if param.StorageType == DB.StorageType.String:
		v = param.AsString()
		return v if v is not None else ""
	elif param.StorageType == DB.StorageType.Integer:
		return str(param.AsInteger())
	elif param.StorageType == DB.StorageType.Double:
		try:
			from Autodesk.Revit.DB import UnitUtils, ForgeTypeId
			spec = param.Definition.GetDataType()
			try:
				display = UnitUtils.ConvertFromInternalUnits(param.AsDouble(), param.GetUnitTypeId())
				return "{:.4g}".format(display)
			except Exception:
				pass
		except Exception:
			pass
		return "{:.4g}".format(param.AsDouble())
	elif param.StorageType == DB.StorageType.ElementId:
		eid = param.AsElementId()
		try:
			return str(_elem_id_int(eid))
		except Exception:
			return ""
	return ""


def copy_param_value(source_param, target_param):
	"""Copy value from source_param to target_param. Returns True on success."""
	from pyrevit import DB
	if source_param is None or target_param is None:
		return False
	if target_param.IsReadOnly:
		return False
	if source_param.StorageType != target_param.StorageType:
		return False
	try:
		st = source_param.StorageType
		if st == DB.StorageType.String:
			target_param.Set(source_param.AsString() or "")
		elif st == DB.StorageType.Integer:
			target_param.Set(source_param.AsInteger())
		elif st == DB.StorageType.Double:
			target_param.Set(source_param.AsDouble())
		elif st == DB.StorageType.ElementId:
			target_param.Set(source_param.AsElementId())
		else:
			return False
		return True
	except Exception:
		return False


# ---------------------------------------------------------------------------
# Category / selection validation
# ---------------------------------------------------------------------------

def validate_same_category(source_elem, targets):
	"""Split targets into (valid, invalid) based on matching source category."""
	source_cat_id = get_element_category_id(source_elem)
	valid = []
	invalid = []
	for t in targets:
		t_cat_id = get_element_category_id(t)
		if source_cat_id is None or t_cat_id is None:
			invalid.append(t)
		elif source_cat_id == t_cat_id:
			valid.append(t)
		else:
			invalid.append(t)
	return valid, invalid


# ---------------------------------------------------------------------------
# Parameter collection
# ---------------------------------------------------------------------------

def collect_source_params(source_elem, target_elems):
	"""Collect instance parameters from source, with writability check against targets.

	Returns a sorted list of dicts: {name, value, is_writable}
	Writable = not read-only on source AND at least one target has the same param writable.
	"""
	params = []
	seen = set()

	for p in source_elem.Parameters:
		name = p.Definition.Name
		if name in seen:
			continue
		seen.add(name)

		value_str = get_param_value_str(p)

		if p.IsReadOnly:
			params.append({"name": name, "value": value_str, "is_writable": False})
			continue

		# Check writability on targets
		any_target_writable = False
		for t in target_elems:
			tp = t.LookupParameter(name)
			if tp is not None and not tp.IsReadOnly and tp.StorageType == p.StorageType:
				any_target_writable = True
				break

		params.append({"name": name, "value": value_str, "is_writable": any_target_writable})

	# Writable params first, then alphabetically within each group
	params.sort(key=lambda x: (0 if x["is_writable"] else 1, x["name"].lower()))
	return params


# ---------------------------------------------------------------------------
# Main tool entry point
# ---------------------------------------------------------------------------

def run_match_property(script_dir, lib_path):
	"""Main entry point called from the pushbutton script."""
	if lib_path not in sys.path:
		sys.path.insert(0, lib_path)

	import WWP_uiUtils as ui
	from pyrevit import DB

	try:
		from Autodesk.Revit.UI.Selection import ObjectType
		uidoc = __revit__.ActiveUIDocument
		doc = uidoc.Document

		# ------------------------------------------------------------------
		# Step 1: Resolve source + target elements
		# ------------------------------------------------------------------
		pre_selection = list(uidoc.Selection.GetElementIds())
		pre_elems = [doc.GetElement(eid) for eid in pre_selection]
		pre_elems = [e for e in pre_elems if e is not None]

		source_elem = None
		target_elems = []

		if len(pre_elems) >= 2:
			# First selected element = source; remaining = targets
			source_elem = pre_elems[0]
			candidates = pre_elems[1:]
			target_elems, invalid = validate_same_category(source_elem, candidates)
			if invalid:
				cat = get_element_category_name(source_elem)
				ui.uiUtils_alert(
					"{} element(s) skipped - not the same category as source ({}).".format(
						len(invalid), cat
					),
					title="Match Property",
				)
			if not target_elems:
				ui.uiUtils_alert(
					"No valid target elements. Targets must match the source category.",
					title="Match Property",
				)
				return

		elif len(pre_elems) == 1:
			# One element pre-selected: use as source, then pick targets
			source_elem = pre_elems[0]
			cat = get_element_category_name(source_elem)
			name = get_element_display_name(source_elem, doc)
			ui.uiUtils_alert(
				"Source: {} - {}\n\nClick OK, then pick target elements in the view.\n"
				"Only {} elements will be accepted.".format(cat, name, cat),
				title="Match Property",
			)
			try:
				refs = uidoc.Selection.PickObjects(
					ObjectType.Element,
					"Pick target elements (same category: {})".format(cat),
				)
				candidates = [doc.GetElement(r.ElementId) for r in refs]
			except Exception:
				return  # User cancelled

			target_elems, invalid = validate_same_category(source_elem, candidates)
			if invalid:
				ui.uiUtils_alert(
					"{} element(s) skipped - category mismatch.".format(len(invalid)),
					title="Match Property",
				)
			if not target_elems:
				ui.uiUtils_alert("No valid target elements selected.", title="Match Property")
				return

		else:
			# Nothing pre-selected: pick source then targets
			try:
				ref = uidoc.Selection.PickObject(
					ObjectType.Element,
					"Pick the SOURCE element",
				)
				source_elem = doc.GetElement(ref.ElementId)
			except Exception:
				return  # User cancelled

			cat = get_element_category_name(source_elem)
			name = get_element_display_name(source_elem, doc)
			ui.uiUtils_alert(
				"Source: {} - {}\n\nClick OK, then pick target elements in the view.\n"
				"Only {} elements will be accepted.".format(cat, name, cat),
				title="Match Property",
			)
			try:
				refs = uidoc.Selection.PickObjects(
					ObjectType.Element,
					"Pick target elements (same category: {})".format(cat),
				)
				candidates = [doc.GetElement(r.ElementId) for r in refs]
			except Exception:
				return  # User cancelled

			target_elems, invalid = validate_same_category(source_elem, candidates)
			if invalid:
				ui.uiUtils_alert(
					"{} element(s) skipped - category mismatch.".format(len(invalid)),
					title="Match Property",
				)
			if not target_elems:
				ui.uiUtils_alert("No valid target elements selected.", title="Match Property")
				return

		# ------------------------------------------------------------------
		# Step 2: Collect parameters from source
		# ------------------------------------------------------------------
		params = collect_source_params(source_elem, target_elems)
		if not params:
			ui.uiUtils_alert(
				"No instance parameters found on the source element.",
				title="Match Property",
			)
			return

		# ------------------------------------------------------------------
		# Step 3: Show parameter selection dialog
		# ------------------------------------------------------------------
		cat_name = get_element_category_name(source_elem)
		src_name = get_element_display_name(source_elem, doc)
		source_info = "{}\n{}".format(cat_name, src_name)
		target_info = "{} element(s) selected\nCategory: {}".format(len(target_elems), cat_name)

		selected_params = ui.uiUtils_match_property_select_params(
			source_info=source_info,
			target_info=target_info,
			param_names=[p["name"] for p in params],
			param_values=[p["value"] for p in params],
			param_writable=[p["is_writable"] for p in params],
		)

		if selected_params is None:
			return  # Cancelled
		if not selected_params:
			ui.uiUtils_alert("No parameters selected. Nothing was changed.", title="Match Property")
			return

		# ------------------------------------------------------------------
		# Step 4: Apply parameter values
		# ------------------------------------------------------------------
		transaction = DB.Transaction(doc, "Match Property")
		transaction.Start()
		try:
			matched = 0
			skipped = 0
			for target in target_elems:
				for pname in selected_params:
					sp = source_elem.LookupParameter(pname)
					tp = target.LookupParameter(pname)
					if sp is not None and tp is not None:
						if copy_param_value(sp, tp):
							matched += 1
						else:
							skipped += 1
					else:
						skipped += 1
			transaction.Commit()
		except Exception:
			transaction.RollBack()
			raise

		msg = "Done. Matched {} value(s) across {} element(s).".format(matched, len(target_elems))
		if skipped > 0:
			msg += "\n{} value(s) skipped (read-only, type mismatch, or parameter not present on target).".format(skipped)
		ui.uiUtils_alert(msg, title="Match Property")

	except Exception as ex:
		ui.uiUtils_alert("Error: {}".format(str(ex)), title="Match Property")
