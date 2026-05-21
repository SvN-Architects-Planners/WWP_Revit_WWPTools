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


def _get_active_sheet(doc):
	active_view = doc.ActiveView
	return active_view if isinstance(active_view, DB.ViewSheet) else None


def main():
	ui = load_uiutils()
	doc = revit.doc

	sheet = _get_active_sheet(doc)
	if not sheet:
		ui.uiUtils_alert("Open a sheet to delete its views.", title="Delete Views")
		return

	view_ids = list(sheet.GetAllPlacedViews())
	if not view_ids:
		ui.uiUtils_alert("No views found on the current sheet.", title="Delete Views")
		return

	message = "Delete {} view(s) from this sheet and the project?".format(len(view_ids))
	if not ui.uiUtils_confirm(message, title="Delete Views"):
		return

	id_list = List[DB.ElementId]()
	for view_id in view_ids:
		id_list.Add(view_id)

	transaction = DB.Transaction(doc, "Delete Views from Sheet")
	transaction.Start()
	try:
		deleted = doc.Delete(id_list)
		deleted_count = len(deleted) if deleted is not None else 0
	finally:
		if transaction.HasStarted():
			transaction.Commit()

	ui.uiUtils_alert(
		"Deleted {} element(s).".format(deleted_count),
		title="Delete Views",
	)


if __name__ == "__main__":
	try:
		main()
	except Exception:
		ui = load_uiutils()
		ui.uiUtils_alert(traceback.format_exc(), title="Delete Views")
