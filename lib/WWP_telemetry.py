import hashlib
import json
import os
import time
import uuid

try:
    import urllib.request as _urlrequest
except Exception:
    try:
        import urllib2 as _urlrequest
    except Exception:
        _urlrequest = None

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

from WWP_versioning import get_installed_version


_APP_NAME = "WWPTools"
_IDENTITY_FILE = "telemetry.identity.json"
_CONFIG_FILE = "telemetry.config.json"
_PENDING_FILE = "telemetry.pending.jsonl"
_ARCHIVE_DIR = "telemetry-archive"


def _appdata_root():
    root = os.environ.get("APPDATA")
    if not root:
        root = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(root, "pyRevit", _APP_NAME)


def _ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def _telemetry_root():
    path = os.path.join(_appdata_root(), "telemetry")
    _ensure_dir(path)
    return path


def _config_path():
    return os.path.join(_telemetry_root(), _CONFIG_FILE)


def _identity_path():
    return os.path.join(_telemetry_root(), _IDENTITY_FILE)


def _pending_path():
    return os.path.join(_telemetry_root(), _PENDING_FILE)


def _archive_path():
    archive_root = os.path.join(_telemetry_root(), _ARCHIVE_DIR)
    _ensure_dir(archive_root)
    return os.path.join(archive_root, "events-{}.jsonl".format(time.strftime("%Y-W%W", time.gmtime())))


def _extension_root():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _read_json_file(path, default=None):
    if not path or not os.path.isfile(path):
        return {} if default is None else default
    try:
        with open(path, "r") as data_file:
            data = json.load(data_file)
        return data
    except Exception:
        return {} if default is None else default


def _write_json_file(path, payload):
    _ensure_dir(os.path.dirname(path))
    with open(path, "w") as data_file:
        json.dump(payload, data_file, indent=2, sort_keys=True)


def _stable_hash(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _load_identity():
    identity = _read_json_file(_identity_path(), default={})
    if not isinstance(identity, dict):
        identity = {}

    install_id = str(identity.get("install_id") or "").strip()
    if not install_id:
        install_id = uuid.uuid4().hex

    user_name = "{}\\{}".format(
        os.environ.get("USERDOMAIN") or "",
        os.environ.get("USERNAME") or "",
    ).strip("\\")
    machine_name = os.environ.get("COMPUTERNAME") or ""

    identity["install_id"] = install_id
    identity["user_id"] = _stable_hash(user_name)
    identity["machine_id"] = _stable_hash(machine_name)
    identity["created_utc"] = identity.get("created_utc") or _utc_now()

    _write_json_file(_identity_path(), identity)
    return identity


def _load_config():
    config = _read_json_file(_config_path(), default={})
    if not isinstance(config, dict):
        config = {}

    endpoint_url = str(
        os.environ.get("WWPTOOLS_TELEMETRY_URL")
        or config.get("endpoint_url")
        or ""
    ).strip()
    api_key = str(
        os.environ.get("WWPTOOLS_TELEMETRY_KEY")
        or config.get("api_key")
        or ""
    ).strip()

    enabled = config.get("enabled", True)
    try:
        timeout_sec = max(1, int(config.get("timeout_sec", 2)))
    except Exception:
        timeout_sec = 2
    try:
        batch_size = max(1, min(100, int(config.get("batch_size", 25))))
    except Exception:
        batch_size = 25

    return {
        "enabled": bool(enabled),
        "endpoint_url": endpoint_url,
        "api_key": api_key,
        "timeout_sec": timeout_sec,
        "batch_size": batch_size,
    }


def _append_jsonl(path, payload):
    _ensure_dir(os.path.dirname(path))
    with open(path, "a") as stream:
        stream.write(json.dumps(payload, sort_keys=True))
        stream.write("\n")


def _is_wwptools_command(command_path, command_extension):
    extension_name = str(command_extension or "").strip().lower()
    if extension_name in ("wwptools", "wwptools.extension"):
        return True

    normalized_path = os.path.normpath(str(command_path or "").strip())
    if not normalized_path:
        return False

    extension_root = _extension_root().lower()
    return normalized_path.lower().startswith(extension_root)


def _safe_exec_value(name):
    if EXEC_PARAMS is None:
        return ""
    try:
        return getattr(EXEC_PARAMS, name) or ""
    except Exception:
        return ""


def _relative_tool_path(command_path):
    normalized_path = os.path.normpath(str(command_path or "").strip())
    if not normalized_path:
        return ""

    try:
        relative = os.path.relpath(normalized_path, _extension_root())
    except Exception:
        relative = normalized_path
    return relative.replace("\\", "/")


def _revit_version():
    try:
        return str(__revit__.Application.VersionNumber)
    except Exception:
        return ""


def _revit_username():
    try:
        name = str(__revit__.Application.Username or "").strip()
        if name:
            return name
    except Exception:
        pass
    return os.environ.get("USERNAME") or ""


def _build_event(event_type, extra_data=None):
    identity = _load_identity()
    payload = {
        "event_id": uuid.uuid4().hex,
        "event_type": event_type,
        "timestamp_utc": _utc_now(),
        "install_id": identity.get("install_id") or "",
        "user_id": identity.get("user_id") or "",
        "user_name": _revit_username(),
        "machine_id": identity.get("machine_id") or "",
        "extension_version": get_installed_version("dev"),
        "revit_version": _revit_version(),
    }
    if isinstance(extra_data, dict):
        payload.update(extra_data)
    return payload


def _queue_event(payload):
    config = _load_config()
    if not config.get("enabled"):
        return

    _append_jsonl(_archive_path(), payload)
    if config.get("endpoint_url"):
        _append_jsonl(_pending_path(), payload)


def flush_pending_events():
    config = _load_config()
    if not config.get("enabled"):
        return 0

    endpoint_url = config.get("endpoint_url") or ""
    if not endpoint_url or _urlrequest is None:
        return 0

    pending_path = _pending_path()
    if not os.path.isfile(pending_path):
        return 0

    try:
        with open(pending_path, "r") as pending_file:
            lines = [line.rstrip("\r\n") for line in pending_file if line.strip()]
    except Exception:
        return 0

    if not lines:
        return 0

    batch_size = int(config.get("batch_size") or 25)
    batch_lines = lines[:batch_size]
    events = []
    for line in batch_lines:
        try:
            events.append(json.loads(line))
        except Exception:
            continue

    if not events:
        return 0

    payload = json.dumps({"events": events}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "WWPTools-Telemetry/{}".format(get_installed_version("dev")),
    }
    api_key = config.get("api_key") or ""
    if api_key:
        headers["X-WWPTools-Key"] = api_key

    try:
        request = _urlrequest.Request(endpoint_url, data=payload, headers=headers)
        response = _urlrequest.urlopen(request, timeout=float(config.get("timeout_sec") or 2))
        status_code = getattr(response, "status", None) or getattr(response, "code", None) or 200
        if int(status_code) < 200 or int(status_code) >= 300:
            return 0
    except Exception:
        return 0

    remaining_lines = lines[len(batch_lines):]
    try:
        with open(pending_path, "w") as pending_file:
            if remaining_lines:
                pending_file.write("\n".join(remaining_lines))
                pending_file.write("\n")
    except Exception:
        return 0

    return len(events)


def track_app_init():
    try:
        payload = _build_event("app-init")
        _queue_event(payload)
        flush_pending_events()
    except Exception:
        pass


def track_current_command():
    try:
        command_path = _safe_exec_value("command_path")
        command_name = _safe_exec_value("command_name")
        command_bundle = _safe_exec_value("command_bundle")
        command_extension = _safe_exec_value("command_extension")

        if not _is_wwptools_command(command_path, command_extension):
            return False

        payload = _build_event(
            "command-exec",
            {
                "command_name": str(command_name or "").strip(),
                "command_bundle": str(command_bundle or "").strip(),
                "command_extension": str(command_extension or "").strip(),
                "tool_key": _relative_tool_path(command_path),
            },
        )
        _queue_event(payload)
        flush_pending_events()
        return True
    except Exception:
        return False
