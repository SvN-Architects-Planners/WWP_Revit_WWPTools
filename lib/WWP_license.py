import json
import os
import re

_LICENSE_FILE = os.path.join(
    os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming")),
    "pyRevit", "WWPTools", "license.json",
)
_ALLOWED_DOMAIN = "wwparchitects.com"
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@wwparchitects\.com$", re.IGNORECASE)


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
    return bool(email) and bool(_EMAIL_PATTERN.match(str(email).strip()))


def is_licensed():
    data = _load()
    return data is not None and _valid(data.get("email", ""))


def activate(email):
    if _valid(email):
        _save(str(email).strip().lower())
        return True
    return False


def require_license():
    """Prompt once for a local WWPTools license and return True only when valid."""
    if is_licensed():
        return True

    try:
        from pyrevit import forms

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
            return True

        if email:
            forms.alert(
                "Activation failed.\n\n"
                "'{}' is not a valid @wwparchitects.com address.\n\n"
                "This tool will not run.".format(email.strip()),
                title="WWPTools Activation Failed",
            )
        else:
            forms.alert(
                "No email entered. This tool will not run.",
                title="WWPTools Activation Required",
            )

        return False

    except Exception:
        return False
