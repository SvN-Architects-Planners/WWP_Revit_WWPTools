__context__ = "zero-doc"

import os
import runpy


script_dir = os.path.dirname(__file__)
updater_script = os.path.normpath(
    os.path.join(script_dir, "..", "UpdateWWPTools.pushbutton", "UpdateWWPTools-script.py")
)

runpy.run_path(
    updater_script,
    init_globals={"MANUAL_GENERATE_UPDATER": True},
    run_name="__main__",
)
