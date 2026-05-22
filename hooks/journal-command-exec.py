# -*- coding: utf-8 -*-
import sys
import os
import traceback

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    if EXEC_PARAMS is None:
        raise SystemExit

    command_name = str(getattr(EXEC_PARAMS, "command_name", "") or "").strip()
    command_extension = str(getattr(EXEC_PARAMS, "command_extension", "") or "").strip()

    # Filter to WWPTools commands only
    if command_extension.lower() not in ("wwptools", "wwptools.extension", "wwp_revit_wwptools"):
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

    lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    import WWP_telemetry
    if success:
        WWP_telemetry.track_use(command_name)
    else:
        WWP_telemetry.track_failed_command(command_name, error_msg)

except SystemExit:
    pass
except Exception:
    pass
