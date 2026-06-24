import importlib
import os
import re
import sys
import traceback

from pyrevit import DB, revit
def load_uiutils():
	script_dir = os.path.dirname(__file__)
	lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
	if lib_path not in sys.path:
		sys.path.append(lib_path)
	import WWP_uiUtils as ui
	if not hasattr(ui, "uiUtils_select_sheet_renumber_inputs_with_list"):
		try:
			ui = importlib.reload(ui)
		except Exception:
			pass
	return ui


def split_prefix_numeric(text):
	prefix = ""
	numeric = ""
	for ch in text or "":
		if ch.isdigit():
			numeric += ch
		else:
			prefix += ch
	return prefix, numeric


def build_new_numbers(starting_str, count):
	prefix, numeric = split_prefix_numeric(starting_str.strip())
	if not numeric:
		numeric = "1"
	starting_number = int(numeric)
	width = len(numeric)
	return [
		"{}{}".format(prefix, str(i).zfill(width))
		for i in range(starting_number, starting_number + count)
	]


def parse_iso19650_pattern(pattern):
	"""Parse '515T[200]D1' -> ('515T', '200', 'D1'). Returns None if no [nnn] found."""
	m = re.match(r'^(.*?)\[(\d+)\](.*)$', pattern.strip())
	if not m:
		return None
	return m.group(1), m.group(2), m.group(3)


def build_new_numbers_iso19650(prefix, starting_str, suffix, count):
	width = len(starting_str)
	starting_number = int(starting_str)
	return [
		"{}{}{}".format(prefix, str(i).zfill(width), suffix)
		for i in range(starting_number, starting_number + count)
	]


def collect_sheets(doc):
	sheets = []
	for view in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Sheets):
		if not isinstance(view, DB.ViewSheet):
			continue
		sheets.append(view)
	return sheets


def main():
	ui = load_uiutils()
	if not hasattr(ui, "uiUtils_select_sheet_renumber_inputs_with_list"):
		ui.uiUtils_alert(
			"UI helper uiUtils_select_sheet_renumber_inputs_with_list is unavailable. Try reloading pyRevit.",
			title="Renumber Sheets",
		)
		return

	doc = revit.doc
	sheets = collect_sheets(doc)
	if not sheets:
		ui.uiUtils_alert("No sheets found.", title="Renumber Sheets")
		return

	sorted_sheets = sorted(sheets, key=lambda s: s.SheetNumber or "")
	display_items = [
		"{} - {}".format(s.SheetNumber or "", s.Name or "")
		for s in sorted_sheets
	]
	combined_inputs = ui.uiUtils_select_sheet_renumber_inputs_with_list(
		display_items,
		title="Renumber Sheets",
		prompt="Select sheets to renumber:",
		starting_label="Starting Number",
		cancel_text="Cancel",
		width=980,
		height=620,
	)
	if not combined_inputs:
		return
	selected_indices = combined_inputs.get("selected_indices") or []
	starting_number = combined_inputs.get("starting_number", "")

	if not selected_indices:
		return

	iso19650_mode = combined_inputs.get("iso19650_mode", False)

	if not starting_number.strip():
		ui.uiUtils_alert("Starting Number is required.", title="Renumber Sheets")
		return

	selected_sheets = [sorted_sheets[i] for i in selected_indices]

	if iso19650_mode:
		parsed = parse_iso19650_pattern(starting_number)
		if not parsed:
			ui.uiUtils_alert(
				"Pattern must contain a bracketed segment, e.g. 515T[200]D1",
				title="Renumber Sheets",
			)
			return
		prefix, numeric_start, suffix = parsed
		new_numbers = build_new_numbers_iso19650(prefix, numeric_start, suffix, len(selected_sheets))
	else:
		_, _numeric_part = split_prefix_numeric(starting_number.strip())
		if not _numeric_part:
			ui.uiUtils_alert("Starting Sheet Number must contain a number (e.g. A100 or 100).", title="Renumber Sheets")
			return
		new_numbers = build_new_numbers(starting_number, len(selected_sheets))

	selected_set = {s.SheetNumber for s in selected_sheets if s.SheetNumber}
	all_numbers  = {s.SheetNumber for s in sheets if s.SheetNumber}
	conflicts    = [n for n in new_numbers if n in all_numbers - selected_set]
	if conflicts:
		ui.uiUtils_alert(
			"These sheet numbers are already in use:\n{}{}".format(
				"\n".join(conflicts[:20]),
				"\n... and {} more".format(len(conflicts) - 20) if len(conflicts) > 20 else "",
			),
			title="Renumber Sheets",
		)
		return

	with revit.Transaction("Renumber Sheets"):
		for idx, sheet in enumerate(selected_sheets):
			sheet.SheetNumber = "_tmp_{}".format(idx)
		for sheet, new_value in zip(selected_sheets, new_numbers):
			sheet.SheetNumber = new_value


if __name__ == "__main__":
	_err_ui = load_uiutils()
	try:
		main()
		import WWP_telemetry
		WWP_telemetry.track_use("RenumberSheets")
	except Exception:
		_err_ui.uiUtils_alert(traceback.format_exc(), title="Renumber Sheets")
