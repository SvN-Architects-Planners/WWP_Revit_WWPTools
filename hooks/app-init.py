# import pyrevit libraries
import os
import clr
from pyrevit import forms, script
from WWP_msgUtils import *
import json
import re
from datetime import datetime
from WWP_compat import Request, decode_to_text, urlopen

try:
	import WWP_telemetry
	WWP_telemetry.track_app_init()
except Exception:
	pass

# Register runtime hooks manually — pyRevit's new_loader (C# path) skips
# hooks.register_hooks(), so command-exec.py never fires without this.
try:
	from pyrevit.loader import hooks as _hooks
	from pyrevit import framework, HOST_APP
	import traceback as _tb
	_debug_log = os.path.join(os.environ.get("APPDATA", ""), "pyRevit", "WWPTools", "hook_debug.txt")
	try:
		_handler = _hooks.get_hooks_handler()
		_hooks_dir = os.path.dirname(__file__)
		_lib_dir = os.path.normpath(os.path.join(_hooks_dir, "..", "lib"))
		_hook_path = os.path.join(_hooks_dir, "command-exec.py")
		_handler.RegisterHook(
			uniqueId="wwp_revit_wwptools.command-exec.py",
			eventName="command-exec",
			eventTarget="",
			scriptPath=_hook_path,
			searchPaths=framework.Array[str]([_lib_dir]),
			extensionName="WWP_Revit_WWPTools",
		)
		_hooks.activate()
		with open(_debug_log, "w") as _f:
			_f.write("OK: handler={} hook_path={}\n".format(_handler, _hook_path))
	except Exception as _e:
		with open(_debug_log, "w") as _f:
			_f.write("ERROR: {}\n{}\n".format(_e, _tb.format_exc()))
except Exception:
	pass

# check if notifications are disabled
if msgUtils_muted():
	script.exit()

# Get icon file (doesn't work)
# curPath = script.get_script_path()
# remPath = curPath.split('WWPTools.tab')[0]
# icoFile = remPath + r'bin\Graphics\ico256_WWP.ico'

# Display the message to the user
icon_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib", "WWPtools-logo.png"))
toolbar_title = "WWP_Tool"
toolbar_msg = "Toolbar has been loaded!"

# ----------------------------------------------------
# Update check (GitHub latest release)
# ----------------------------------------------------
def _parse_semver(value):
	if not value:
		return None
	match = re.search(r"(\d+)\.(\d+)\.(\d+)", value)
	if not match:
		return None
	return tuple(int(x) for x in match.groups())


def _get_local_version():
	try:
		repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
		version_files = [
			os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib", "WWPTools.version.json")),
			os.path.join(repo_root, "WWPTools.extension", "lib", "WWPTools.version.json"),
		]
		for version_file in version_files:
			if os.path.exists(version_file):
				with open(version_file, "r") as fp:
					data = json.load(fp)
				version_value = data.get("version") if isinstance(data, dict) else None
				parsed = _parse_semver(version_value)
				if parsed:
					return parsed

		changelog = os.path.join(repo_root, "CHANGELOG.md")
		if not os.path.exists(changelog):
			return None
		with open(changelog, "r") as fp:
			content = fp.read()
		versions = re.findall(r"^## \\[(\\d+\\.\\d+\\.\\d+)\\]", content, flags=re.MULTILINE)
		if not versions:
			return None
		parsed = [_parse_semver(v) for v in versions]
		parsed = [v for v in parsed if v]
		if not parsed:
			return None
		return max(parsed)
	except Exception:
		return None


def _get_latest_release_version():
	try:
		req = Request(
			"https://api.github.com/repos/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest",
			headers={"User-Agent": "WWPTools"},
		)
		with urlopen(req, timeout=2) as resp:
			data = json.loads(decode_to_text(resp.read(), "utf-8"))
		tag = data.get("tag_name") or data.get("name")
		return _parse_semver(tag)
	except Exception:
		return None


def _should_check_updates():
	try:
		cache = script.load_data("wwptools_update_check", this_project=False)
		if not cache:
			return True
		last_date = cache.get("date")
		today = datetime.now().strftime("%Y-%m-%d")
		return last_date != today
	except Exception:
		return True


def _mark_checked():
	try:
		script.save_data("wwptools_update_check", {"date": datetime.now().strftime("%Y-%m-%d")}, this_project=False)
	except Exception:
		pass


try:
	if _should_check_updates():
		local_ver = _get_local_version()
		latest_ver = _get_latest_release_version()
		_mark_checked()
		if local_ver and latest_ver:
			local_str = "{}.{}.{}".format(*local_ver)
			latest_str = "{}.{}.{}".format(*latest_ver)
			if latest_ver > local_ver:
				toolbar_msg = "Toolbar loaded. Your version ({}) is outdated.".format(local_str)
				forms.toaster.send_toast(
					"New version available: {}".format(latest_str),
					title="WWPTools Update",
					appid="WWP Architects + Planners",
					icon=icon_path if os.path.exists(icon_path) else None,
					click="https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest",
					actions=None,
				)
			else:
				toolbar_msg = "Toolbar loaded. You are running the latest version ({})".format(local_str)
except Exception:
	pass

try:
	if os.path.exists(icon_path):
		forms.toaster.send_toast(
			toolbar_msg,
			title=toolbar_title,
			appid="WWP Architects + Planners",
			icon=icon_path,
			click=None,
			actions=None,
		)
	else:
		forms.toast(
			toolbar_msg,
			toolbar_title,
			appid="WWP Architects + Planners",
			actions=None,
		)
except Exception:
	pass
