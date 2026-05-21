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


def _get_excluded_view_types():
	excluded = {DB.ViewType.DrawingSheet}
	for name in ("ProjectBrowser", "SystemBrowser", "Internal"):
		view_type = getattr(DB.ViewType, name, None)
		if view_type is not None:
			excluded.add(view_type)
	return excluded


def collect_views_not_on_sheets(doc):
	placed_view_ids = set()
	for sheet in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
		for view_id in sheet.GetAllPlacedViews():
			placed_view_ids.add(view_id)

	excluded_types = _get_excluded_view_types()
	active_view = doc.ActiveView
	active_id = active_view.Id if active_view else None

	candidates = []
	for view in DB.FilteredElementCollector(doc).OfClass(DB.View):
		if view.IsTemplate:
			continue
		if view.ViewType in excluded_types:
			continue
		if active_id and view.Id == active_id:
			continue
		if view.Id in placed_view_ids:
			continue
		candidates.append(view)
	return candidates


def main():
	ui = load_uiutils()
	doc = revit.doc

	views_to_delete = collect_views_not_on_sheets(doc)
	if not views_to_delete:
		ui.uiUtils_alert("No views found that are not on sheets.", title="Delete Views")
		return

	message = "Delete {} view(s) not placed on sheets?".format(len(views_to_delete))
	if not ui.uiUtils_confirm(message, title="Delete Views"):
		return

	transaction = DB.Transaction(doc, "Delete Views Not On Sheets")
	transaction.Start()
	try:
		id_list = List[DB.ElementId]()
		for view in views_to_delete:
			id_list.Add(view.Id)
		deleted = doc.Delete(id_list)
		deleted_count = len(deleted) if deleted is not None else 0
	finally:
		if transaction.HasStarted():
			transaction.Commit()

	ui.uiUtils_alert(
		"Deleted {} view(s).".format(deleted_count),
		title="Delete Views",
	)


if __name__ == "__main__":
	try:
		main()
	except Exception:
		ui = load_uiutils()
		ui.uiUtils_alert(traceback.format_exc(), title="Delete Views")
