"""
WWP_telemetry.py — Per-script usage logging to Neon Postgres via Vercel endpoint.
Fire-and-forget. Never raises. Queues locally if offline, flushes on next success.

Config (pick one):
  Env var  : WWPTOOLS_TELEMETRY_URL=https://your-project.vercel.app/api/...
  JSON file: %APPDATA%\pyRevit\WWPTools\telemetry\telemetry.config.json
             {"endpoint_url": "https://...", "enabled": true}
"""
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

_PENDING_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
    "WWP", "pending_script_logs.jsonl",
)

_CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
    "pyRevit", _APP_NAME, "telemetry", "telemetry.config.json",
)

_endpoint = None
_enabled = True
_config_loaded = False
_config_lock = threading.Lock()


def _load_config():
    global _endpoint, _enabled, _config_loaded
    with _config_lock:
        if _config_loaded:
            return
        _config_loaded = True
        url = (os.environ.get("WWPTOOLS_TELEMETRY_URL") or "").strip()
        enabled = True
        if not url:
            try:
                with open(_CONFIG_PATH, "r") as f:
                    cfg = json.load(f)
                url = str(cfg.get("endpoint_url") or "").strip()
                enabled = bool(cfg.get("enabled", True))
            except Exception:
                pass
        _endpoint = url or None
        _enabled = enabled


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


def _get_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return None


def _post(entry):
    if not _endpoint:
        raise Exception("no endpoint configured")
    data = json.dumps(entry).encode("utf-8")
    req = Request(_endpoint, data=data, headers={"Content-Type": "application/json"})
    req.get_method = lambda: "POST"
    urlopen(req, timeout=5)


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
        pass  # leave file intact — retry on next execution


def _worker(entry):
    try:
        _post(entry)
        _flush_pending()
    except Exception:
        _queue(entry)


def _fire(script_name, script_type="python", success=True,
          duration_ms=0, error_msg=None, project_number=None):
    _load_config()
    if not _enabled or not _endpoint:
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
    }
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


def track_app_init():
    """Log a session-start entry."""
    try:
        _fire("app-init", script_type="python")
    except Exception:
        pass