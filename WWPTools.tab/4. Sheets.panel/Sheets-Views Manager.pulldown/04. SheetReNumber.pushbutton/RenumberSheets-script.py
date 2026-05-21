import importlib
import os
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


def build_new_numbers(starting_str, count, reference_list):
	prefix, numeric = split_prefix_numeric(starting_str.strip())
	if not numeric:
		numeric = "1"
	starting_number = int(numeric)
	width = len(numeric)
	return [
		"{}{}".format(prefix, str(i).zfill(width))
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
	if not hasattr(ui, "uiUtils_select_sheet_renumber_inputs"):
		ui.uiUtils_alert(
			"UI helper uiUtils_select_sheet_renumber_inputs is unavailable.",
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
	if not starting_number.strip():
		ui.uiUtils_alert("Starting Number is required.", title="Renumber Sheets")
		return

	_, _numeric_part = split_prefix_numeric(starting_number.strip())
	if not _numeric_part:
		ui.uiUtils_alert("Starting Sheet Number must contain a number (e.g. A100 or 100).", title="Renumber Sheets")
		return

	selected_sheets = [sorted_sheets[i] for i in selected_indices]
	sorted_keys = [s.SheetNumber or "" for s in selected_sheets]

	new_numbers = build_new_numbers(starting_number, len(selected_sheets), sorted_keys)

	transaction = DB.Transaction(doc, "Renumber Sheets")
	transaction.Start()
	try:
		for sheet in selected_sheets:
			temp_value = "t{}".format(sheet.SheetNumber or "")
			sheet.SheetNumber = temp_value
		for sheet, new_value in zip(selected_sheets, new_numbers):
			sheet.SheetNumber = new_value
	finally:
		if transaction.HasStarted():
			transaction.Commit()


if __name__ == "__main__":
	try:
		main()
	except Exception:
		ui = load_uiutils()
		ui.uiUtils_alert(traceback.format_exc(), title="Renumber Sheets")
