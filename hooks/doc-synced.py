# -*- coding: utf-8 -*-
import json
import math
import os
from datetime import datetime

from pyrevit import forms, script

try:
	from WWP_msgUtils import msgUtils_muted
except Exception:
	def msgUtils_muted():
		return False


def _wwptools_dir():
	appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
	return os.path.join(appdata, "pyRevit", "WWPTools")


def _sync_state_path():
	return os.path.join(_wwptools_dir(), "sync_start.json")


def _format_number(value):
	return str(int(value)).rjust(2, "0")


def _send_toast(message, title):
	icon_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib", "WWPtools-logo.png"))
	forms.toaster.send_toast(
		message,
		title=title,
		appid="WWP Architects + Planners",
		icon=icon_path if os.path.exists(icon_path) else None,
		click=None,
		actions=None,
	)


if msgUtils_muted():
	script.exit()

try:
	started_at = None
	state_path = _sync_state_path()
	if os.path.exists(state_path):
		with open(state_path, "r") as fp:
			started_at = json.load(fp).get("started_at")

	if started_at:
		start_time = datetime.strptime(started_at.split(".")[0], "%Y-%m-%dT%H:%M:%S")
		elapsed = max(0, int((datetime.now() - start_time).total_seconds()))
	else:
		elapsed = 0

	e_mins = int(math.floor(elapsed / 60))
	e_secs = elapsed % 60
	if e_mins < 1:
		msg_time = "Only " + _format_number(e_secs) + " secs"
		msg_title = "Sync complete"
	elif e_mins > 5:
		msg_time = _format_number(e_mins) + " mins & " + _format_number(e_secs) + " secs"
		msg_title = "Sync complete (slow)"
	else:
		msg_time = _format_number(e_mins) + " mins & " + _format_number(e_secs) + " secs"
		msg_title = "Sync complete"

	if e_mins >= 10:
		msg_time = msg_time + "\nModel health: https://svn-architects-planners-inc.gitbook.io/svn-guidebooks/w7kFyDX0kRTb27slSn93/wwp-technical-guidebook/section-2-or-revit/2.2-or-general-info/2.1.5-or-important-concepts/2.1.5.4-or-revit-health"

	_send_toast(msg_time, msg_title)
except Exception:
	pass
