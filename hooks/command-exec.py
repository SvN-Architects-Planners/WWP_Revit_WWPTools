# -*- coding: utf-8 -*-
import sys
import os
import json
import datetime
import socket
import traceback

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    args = EXEC_PARAMS.event_args if EXEC_PARAMS else None
    command_id = str(args.CommandId.Name) if args else ""

    # Filter to WWPTools commands only
    if "wwp" not in command_id.lower():
        raise SystemExit

    # Detect whether the command raised an error
    exc_type  = getattr(sys, "last_type",  None)
    exc_value = getattr(sys, "last_value", None)
    exc_tb    = getattr(sys, "last_traceback", None)
    success   = exc_type is None
    error_msg = None
    if not success:
        try:
            error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))[:1000]
        except Exception:
            error_msg = str(exc_value)[:1000] if exc_value else "unknown error"

    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    log_path = os.path.join(appdata, "pyRevit", "WWPTools", "script_log.jsonl")
    entry = {
        "logged_at":    datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "user_name":    os.environ.get("USERNAME") or os.environ.get("USER"),
        "machine_name": socket.gethostname(),
        "script_name":  command_id,
        "script_type":  "python",
        "success":      success,
        "error_msg":    error_msg,
    }
    folder = os.path.dirname(log_path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # Send to Neon
    lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    import WWP_telemetry
    if success:
        WWP_telemetry.track_use(command_id)
    else:
        WWP_telemetry.track_failed_command(command_id, error_msg)

except SystemExit:
    pass
except Exception:
    pass
