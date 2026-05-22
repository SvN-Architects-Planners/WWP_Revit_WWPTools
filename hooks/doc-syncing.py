# -*- coding: utf-8 -*-
import json
import os
from datetime import datetime


def _wwptools_dir():
	appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
	return os.path.join(appdata, "pyRevit", "WWPTools")


def _sync_state_path():
	return os.path.join(_wwptools_dir(), "sync_start.json")


try:
	folder = _wwptools_dir()
	if folder and not os.path.isdir(folder):
		os.makedirs(folder)
	state = {
		"started_at": datetime.now().isoformat(),
	}
	with open(_sync_state_path(), "w") as fp:
		json.dump(state, fp)
except Exception:
	pass
