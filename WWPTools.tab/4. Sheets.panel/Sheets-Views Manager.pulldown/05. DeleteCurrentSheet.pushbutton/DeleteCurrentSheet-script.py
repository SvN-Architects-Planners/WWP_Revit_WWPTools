import importlib
import os
import sys
import traceback

from pyrevit import DB, revit
from System.Collections.Generic import List


def load_uiutils():
	script_dir = os.path.dirname(__file__)
	lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
	if lib_path not in sys.path:
		sys.path.append(lib_path)
	import WWP_uiUtils as ui
	if not hasattr(ui, "uiUtils_confirm"):
		try:
			ui = importlib.reload(ui)
		except Exception:
			pass
	return ui


def _get_target_sheets(doc):
	selection = revit.get_selection()
	selected = list(selection.elements) if selection else []
	sheets = [el for el in selected if isinstance(el, DB.ViewSheet)]
	if sheets:
		return sheets
	active_view = doc.ActiveView
	if isinstance(active_view, DB.ViewSheet):
		return [active_view]
	return []


def _collect_sheet_views(sheets):
	view_ids = set()
	for sheet in sheets:
		for view_id in sheet.GetAllPlacedViews():
			view_ids.add(view_id)
	return view_ids


def main():
	ui = load_uiutils()
	doc = revit.doc

	sheets = _get_target_sheets(doc)
	if not sheets:
		ui.uiUtils_alert("Select one or more sheets first.", title="Delete Sheets")
		return

	view_ids = _collect_sheet_views(sheets)
	message = "Delete {} sheet(s) and {} view(s)?".format(len(sheets), len(view_ids))
	if not ui.uiUtils_confirm(message, title="Delete Sheets"):
		return

	id_list = List[DB.ElementId]()
	for view_id in view_ids:
		id_list.Add(view_id)
	for sheet in sheets:
		id_list.Add(sheet.Id)

	transaction = DB.Transaction(doc, "Delete Sheets and Views")
	transaction.Start()
	try:
		deleted = doc.Delete(id_list)
		deleted_count = len(deleted) if deleted is not None else 0
	finally:
		if transaction.HasStarted():
			transaction.Commit()

	ui.uiUtils_alert(
		"Deleted {} element(s).".format(deleted_count),
		title="Delete Sheets",
	)


if __name__ == "__main__":
	try:
		main()
	except Exception:
		ui = load_uiutils()
		ui.uiUtils_alert(traceback.format_exc(), title="Delete Sheets")
