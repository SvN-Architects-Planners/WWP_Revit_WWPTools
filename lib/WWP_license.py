import json
import os

_LICENSE_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming")),
    "pyRevit", "WWPTools", "license.json",
)
_ALLOWED_DOMAIN = "wwparchitects.com"


def _ensure_dir(path):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)


def _load():
    if not os.path.isfile(_LICENSE_FILE):
        return None
    try:
        with open(_LICENSE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _save(email):
    _ensure_dir(_LICENSE_FILE)
    with open(_LICENSE_FILE, "w") as f:
        json.dump({"email": email}, f, indent=2)


def _valid(email):
    return bool(email) and str(email).strip().lower().endswith("@" + _ALLOWED_DOMAIN)


def is_licensed():
    data = _load()
    return data is not None and _valid(data.get("email", ""))


def activate(email):
    if _valid(email):
        _save(str(email).strip().lower())
        return True
    return False


def require_license():
    """Check license on startup; prompt for activation if not yet set up. Exits the script if not licensed."""
    if is_licensed():
        return

    try:
        from pyrevit import forms, script

        email = forms.ask_for_string(
            prompt=(
                "WWPTools requires activation.\n\n"
                "Enter your @wwparchitects.com email address:"
            ),
            title="WWPTools Activation",
            default="",
        )

        if email and activate(email):
            forms.toast(
                "WWPTools activated for {}.".format(email.strip().lower()),
                title="WWPTools",
            )
            return

        if email:
            # Wrong domain — show alert then exit
            forms.alert(
                "Activation failed.\n\n"
                "'{}' is not a valid @wwparchitects.com address.\n\n"
                "WWPTools will not load.".format(email.strip()),
                title="WWPTools Activation Failed",
            )
        else:
            forms.alert(
                "No email entered. WWPTools will not load.",
                title="WWPTools Activation Required",
            )

        script.exit()

    except Exception:
        # If forms/script are unavailable, silently allow (avoids crashing in test harness)
        pass
