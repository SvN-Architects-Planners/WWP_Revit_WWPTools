import sys
import os

try:
    lib_path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "lib"))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)
    import WWP_telemetry
    WWP_telemetry.track_current_command()
except Exception:
    pass