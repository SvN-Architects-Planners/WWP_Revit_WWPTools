import sys
import os
import traceback

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None


def _extension_root():
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _is_wwptools_command(command_path, command_extension):
    if str(command_extension or "").strip().lower() in ("wwptools", "wwptools.extension"):
        return True
    path = os.path.normpath(str(command_path or "").strip())
    if not path:
        return False
    return path.lower().startswith(_extension_root().lower())


try:
    if EXEC_PARAMS is not None:
        command_path = str(getattr(EXEC_PARAMS, "command_path", "") or "").strip()
        command_name = str(getattr(EXEC_PARAMS, "command_name", "") or "").strip()
        command_extension = str(getattr(EXEC_PARAMS, "command_extension", "") or "").strip()

        if _is_wwptools_command(command_path, command_extension):
            lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
            if lib_path not in sys.path:
                sys.path.insert(0, lib_path)
            import WWP_telemetry

            error_parts = traceback.format_exception(
                getattr(sys, "last_type", None),
                getattr(sys, "last_value", None),
                getattr(sys, "last_traceback", None)
            )
            error_msg = "".join(error_parts)
            WWP_telemetry.track_failed_command(command_name or command_path, error_msg[:1000])
except Exception:
    pass
