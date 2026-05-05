# -*- coding: utf-8 -*-
import os
import sys

script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from param_value_renamer_common import run_with_error_dialog_category


if __name__ == "__main__":
    run_with_error_dialog_category(script_dir, lib_path)
