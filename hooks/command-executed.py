# -*- coding: utf-8 -*-
import sys
import os
import json
import datetime
import socket

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    log_path = os.path.join(appdata, "pyRevit", "WWPTools", "script_log.jsonl")

    folder = os.path.dirname(log_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)

    # Capture whatever EXEC_PARAMS has — no filtering, no guards
    entry = {
        "logged_at":    datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_name":    os.environ.get("USERNAME") or os.environ.get("USER"),
        "machine_name": socket.gethostname(),
        "script_name":  str(getattr(EXEC_PARAMS, "command_name", "?") or "?") if EXEC_PARAMS else "NO_EXEC_PARAMS",
        "script_type":  "python",
        "command_ext":  str(getattr(EXEC_PARAMS, "command_extension", "?") or "?") if EXEC_PARAMS else "?",
        "command_path": str(getattr(EXEC_PARAMS, "command_path", "?") or "?") if EXEC_PARAMS else "?",
        "success":      True,
    }

    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Still try Neon upload
    lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    import WWP_telemetry
    WWP_telemetry.track_current_command()
except Exception:
    pass
