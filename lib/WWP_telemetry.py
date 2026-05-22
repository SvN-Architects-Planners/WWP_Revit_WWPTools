# -*- coding: utf-8 -*-
"""WWP_telemetry.py - Per-script usage logging to Neon Postgres via Vercel endpoint."""
import json
import os
import socket
import threading

try:
    from urllib.request import urlopen, Request
except ImportError:
    from urllib2 import urlopen, Request  # type: ignore

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

from WWP_versioning import get_installed_version


_APP_NAME = "WWPTools"
_APPDATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")

_ENDPOINT = "https://wwp-revit-wwp-tools-logger.vercel.app/api/addin-config?key=c5ce6b85-9619-4b24-8db5-6c133534b9f0"

_PENDING_PATH   = os.path.join(_APPDATA, "pyRevit", "WWPTools", "pending_script_logs.jsonl")
_LOCAL_LOG_PATH = os.path.join(_APPDATA, "pyRevit", "WWPTools", "script_log.jsonl")
_PREFS_PATH     = os.path.join(_APPDATA, "pyRevit", _APP_NAME, "user_prefs.json")

_LOCAL_LOG_MAX_LINES = 2000

_user_pref_enabled = None  # None = not yet read from disk
_prefs_lock = threading.Lock()


def is_telemetry_enabled():
    """Returns the user's opt-in preference (default True). Cached after first read."""
    global _user_pref_enabled
    with _prefs_lock:
        if _user_pref_enabled is None:
            try:
                with open(_PREFS_PATH, "r") as f:
                    _user_pref_enabled = bool(json.load(f).get("telemetry_enabled", True))
            except Exception:
                _user_pref_enabled = True
        return _user_pref_enabled


def set_telemetry_enabled(value):
    """Persist the user's opt-in preference and update the in-memory cache."""
    global _user_pref_enabled
    try:
        folder = os.path.dirname(_PREFS_PATH)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        prefs = {}
        try:
            with open(_PREFS_PATH, "r") as f:
                prefs = json.load(f)
        except Exception:
            pass
        prefs["telemetry_enabled"] = bool(value)
        with open(_PREFS_PATH, "w") as f:
            json.dump(prefs, f)
    except Exception:
        pass
    with _prefs_lock:
        _user_pref_enabled = bool(value)


def _extension_root():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _is_wwptools_command(command_path, command_extension):
    if str(command_extension or "").strip().lower() in ("wwptools", "wwptools.extension"):
        return True
    path = os.path.normpath(str(command_path or "").strip())
    if not path:
        return False
    return path.lower().startswith(_extension_root().lower())


def _revit_version():
    try:
        return str(__revit__.Application.VersionNumber)  # type: ignore
    except Exception:
        return None


def _revit_username():
    try:
        name = str(__revit__.Application.Username or "").strip()  # type: ignore
        if name:
            return name
    except Exception:
        pass
    return os.environ.get("USERNAME") or os.environ.get("USER")


# def _pyrevit_version():
#     try:
#         from pyrevit import version as pv
#         return str(pv)
#     except Exception:
#         return None


def _get_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return None


def _post(entry):
    data = json.dumps(entry).encode("utf-8")
    req = Request(_ENDPOINT, data=data, headers={"Content-Type": "application/json"})
    req.get_method = lambda: "POST"
    urlopen(req, timeout=5)


def _write_local_log(entry):
    try:
        folder = os.path.dirname(_LOCAL_LOG_PATH)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(_LOCAL_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
        try:
            with open(_LOCAL_LOG_PATH, "r") as f:
                lines = f.readlines()
            if len(lines) > _LOCAL_LOG_MAX_LINES:
                with open(_LOCAL_LOG_PATH, "w") as f:
                    f.writelines(lines[-_LOCAL_LOG_MAX_LINES:])
        except Exception:
            pass
    except Exception:
        pass


def _queue(entry):
    try:
        folder = os.path.dirname(_PENDING_PATH)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(_PENDING_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _flush_pending():
    if not os.path.exists(_PENDING_PATH):
        return
    try:
        with open(_PENDING_PATH, "r") as f:
            lines = f.readlines()
        for line in lines:
            line = line.strip()
            if line:
                _post(json.loads(line))
        os.remove(_PENDING_PATH)
    except Exception:
        pass  # leave file intact - retry on next execution


def _worker(entry):
    try:
        _post(entry)
        _flush_pending()
    except Exception:
        _queue(entry)


def _fire(script_name, script_type="python", success=True,
          duration_ms=0, error_msg=None, project_number=None):
    if not is_telemetry_enabled():
        return
    entry = {
        "user_name":      _revit_username(),
        "machine_name":   _get_hostname(),
        "script_name":    script_name,
        "script_type":    script_type,
        "revit_version":  _revit_version(),
        "project_number": project_number,
        "duration_ms":    int(duration_ms or 0),
        "success":        bool(success),
        "error_msg":      error_msg,
        # Future fields (enable after Terry adds DB columns):
        # "session_id":      None,
        # "event_type":      script_type or "tool_use",
        # "pyrevit_version": None,
        # "wwptools_version": None,
        # "document_name":   None,
    }
    _write_local_log(entry)
    t = threading.Thread(target=_worker, args=(entry,))
    t.daemon = True
    t.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def track_use(script_name, success=True, duration_ms=0, error_msg=None, project_number=None):
    """Log a single script execution. Call at the end of every script."""
    try:
        _fire(script_name, script_type="python",
              success=success, duration_ms=duration_ms,
              error_msg=error_msg, project_number=project_number)
    except Exception:
        pass


def track_current_command():
    """Auto-detect and log the currently executing pyRevit command."""
    try:
        if EXEC_PARAMS is None:
            return False
        command_path = str(getattr(EXEC_PARAMS, "command_path", "") or "").strip()
        command_name = str(getattr(EXEC_PARAMS, "command_name", "") or "").strip()
        command_extension = str(getattr(EXEC_PARAMS, "command_extension", "") or "").strip()
        if not _is_wwptools_command(command_path, command_extension):
            return False
        _fire(command_name or command_path, script_type="python")
        return True
    except Exception:
        return False


def track_failed_command(script_name, error_msg):
    """Log an unhandled command exception captured by the command-failed hook."""
    try:
        _fire(script_name, success=False, error_msg=error_msg[:1000] if error_msg else None)
    except Exception:
        pass


def track_app_init():
    """Log a session-start entry."""
    try:
        _fire("app-init", script_type="python")
    except Exception:
        pass
