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
    lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)

    command_name = str(getattr(EXEC_PARAMS, "command_name", "") or "") if EXEC_PARAMS else ""
    command_ext  = str(getattr(EXEC_PARAMS, "command_extension", "") or "") if EXEC_PARAMS else ""
    command_path_str = str(getattr(EXEC_PARAMS, "command_path", "") or "") if EXEC_PARAMS else ""

    # Check if this is a WWPTools command by extension name or path
    ext_lower  = command_ext.strip().lower()
    path_lower = command_path_str.replace("\\", "/").lower()
    is_wwp = (ext_lower in ("wwptools", "wwptools.extension", "wwp_revit_wwptools")
              or "wwp_revit_wwptools" in path_lower
              or "wwptools.extension" in path_lower)

    if is_wwp and command_name:
        # Write to local log directly — avoids any silent failure in the telemetry chain
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        log_path = os.path.join(appdata, "pyRevit", "WWPTools", "script_log.jsonl")
        entry = {
            "logged_at":    datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_name":    os.environ.get("USERNAME") or os.environ.get("USER"),
            "machine_name": socket.gethostname(),
            "script_name":  command_name,
            "script_type":  "python",
            "success":      True,
            "error_msg":    None,
        }
        folder = os.path.dirname(log_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

        # Send to Neon via telemetry module
        import WWP_telemetry
        WWP_telemetry.track_current_command()
except Exception:
    pass
