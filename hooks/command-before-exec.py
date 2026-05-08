import os
import sys

try:
    from pyrevit import EXEC_PARAMS
except Exception:
    EXEC_PARAMS = None

try:
    from WWP_license import require_license
except Exception:
    require_license = None


def _is_wwptools_command():
    try:
        if EXEC_PARAMS is None:
            return False
        command_path = getattr(EXEC_PARAMS, "command_path", None)
        if not command_path:
            return False
        command_path = os.path.normcase(os.path.abspath(command_path))
        extension_root = os.path.normcase(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
        return command_path.startswith(extension_root + os.sep)
    except Exception:
        return False


if _is_wwptools_command() and require_license is not None and not require_license():
    sys.exit(0)
