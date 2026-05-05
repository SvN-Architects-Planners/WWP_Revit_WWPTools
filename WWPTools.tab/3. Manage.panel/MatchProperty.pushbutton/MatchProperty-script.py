import os
import sys

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from match_property_common import run_match_property

if __name__ == "__main__":
    run_match_property(script_dir, lib_path)
