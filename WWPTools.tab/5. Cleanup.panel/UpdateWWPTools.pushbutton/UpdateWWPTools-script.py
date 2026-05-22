__context__ = "zero-doc"

import os
import subprocess
import sys
import traceback

from pyrevit import script  # type: ignore
from pyrevit.coreutils import git as pygit  # type: ignore


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)


TITLE = "Update WWPTools"
RELEASES_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest"
SUPPORTED_UPDATE_BRANCHES = ("main", "pyrevit-6.1", "pyrevit-6.4")

# Windows process-creation flags (safe fallback for IronPython)
_DETACHED_PROCESS       = getattr(subprocess, "DETACHED_PROCESS",       0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _revit_ui():
    try:
        import clr  # type: ignore
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit import UI  # type: ignore
        return UI
    except Exception:
        return None


def _alert(message, title=TITLE):
    text = "" if message is None else str(message)
    caption = "" if title is None else str(title)
    UI = _revit_ui()
    if UI is not None:
        try:
            UI.TaskDialog.Show(caption or TITLE, text)
            return
        except Exception:
            pass
    try:
        from pyrevit import forms  # type: ignore
        forms.alert(text, title=caption or TITLE)
    except Exception:
        raise Exception(text)


def _confirm(message, title=TITLE):
    text = "" if message is None else str(message)
    caption = "" if title is None else str(title)
    UI = _revit_ui()
    if UI is not None:
        try:
            dialog = UI.TaskDialog(caption or TITLE)
            dialog.MainInstruction = caption or TITLE
            dialog.MainContent = text
            dialog.CommonButtons = UI.TaskDialogCommonButtons.Yes | UI.TaskDialogCommonButtons.No
            dialog.DefaultButton = UI.TaskDialogResult.Yes
            return dialog.Show() == UI.TaskDialogResult.Yes
        except Exception:
            pass
    try:
        from pyrevit import forms  # type: ignore
        return bool(forms.alert(text, title=caption or TITLE, yes=True, no=True))
    except Exception:
        return False


def _reload_pyrevit():
    try:
        from pyrevit.loader import sessionmgr  # type: ignore
        sessionmgr.reload_pyrevit()
        return True
    except Exception:
        pass
    try:
        from pyrevit.loader import sessionmgr as sm  # type: ignore
        sm.reload_pyrevit()
        return True
    except Exception:
        return False


def _powershell_message_command(title, message):
    safe_title = (title or "").replace("'", "''")
    safe_message = (message or "").replace("'", "''")
    return (
        "powershell -NoProfile -ExecutionPolicy Bypass -STA "
        "-Command \"try {{ "
        "Add-Type -AssemblyName PresentationFramework -ErrorAction Stop; "
        "[System.Windows.MessageBox]::Show('{msg}', '{title}') | Out-Null "
        "}} catch {{}}\""
    ).format(msg=safe_message, title=safe_title)


def _launch_powershell_message(title, message):
    """Launch the PowerShell message externally so it does not block Revit."""
    # Try toast API first (non-blocking visual notification)
    toast_cmd = _powershell_toast_command(title, message)
    try:
        subprocess.Popen(
            toast_cmd,
            shell=True,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except Exception:
        pass

    # Fallback to plain PowerShell MessageBox (detached)
    msg_cmd = _powershell_message_command(title, message)
    try:
        subprocess.Popen(
            msg_cmd,
            shell=True,
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def _powershell_toast_command(title, message, appid="WWP Architects + Planners"):
    """Return a PowerShell command string that shows a Windows toast notification.
    Falls back to a MessageBox if toast APIs are unavailable.
    """
    safe_title = (title or "").replace("'", "''")
    safe_message = (message or "").replace("'", "''")
    safe_appid = (appid or "").replace("'", "''")

    # Use Windows.UI.Notifications via WinRT if available, else fallback to MessageBox
    cmd = (
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"try {{ "
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop; "
        "$xml = '<toast><visual><binding template=\'ToastGeneric\'><text>{title}</text><text>{msg}</text></binding></visual></toast>'; "
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument; $doc.LoadXml($xml); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc); "
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{appid}'); $notifier.Show($toast); "
        "}} catch {{ try {{ Add-Type -AssemblyName PresentationFramework -ErrorAction SilentlyContinue; [System.Windows.MessageBox]::Show('{msg}', '{title}') }} catch {{}} }}\""
    ).format(title=safe_title, msg=safe_message, appid=safe_appid)
    return cmd


def _extension_root():
    return os.path.normpath(os.path.join(script_dir, "..", "..", ".."))


def _latest_tag(repo_root):
    if not _git_cli_available():
        return None
    try:
        tag = _git_output(repo_root, ["describe", "--tags", "--abbrev=0"]).strip()
        return tag if tag else None
    except Exception:
        return None


def _remote_branch(target_branch):
    return "origin/{}".format(target_branch)


def _remote_tag(repo_root, target_branch):
    if not _git_cli_available():
        return None
    try:
        tag = _git_output(
            repo_root,
            ["describe", "--tags", "--abbrev=0", _remote_branch(target_branch)]
        ).strip()
        return tag if tag else None
    except Exception:
        return None


def _incoming_log(repo_root, target_branch, max_lines=10):
    if not _git_cli_available():
        return ""
    try:
        log = _git_output(
            repo_root,
            ["log", "--oneline", "HEAD..{}".format(_remote_branch(target_branch))]
        ).strip()
        lines = log.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["... and {} more".format(len(lines) - max_lines)]
        return "\n".join(lines)
    except Exception:
        return ""


def _incoming_changed_files(repo_root, target_branch):
    if not _git_cli_available():
        return []
    try:
        output = _git_output(
            repo_root,
            ["diff", "--name-only", "HEAD..{}".format(_remote_branch(target_branch))]
        ).strip()
        return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]
    except Exception:
        return []


def _update_needs_revit_close(changed_files):
    for path in changed_files or []:
        lower_path = path.lower()
        if lower_path.endswith(".dll"):
            return True
    return False


def _working_tree_dirty(repo_root):
    if not _git_cli_available():
        return False
    try:
        return bool(_git_output(repo_root, ["status", "--porcelain"]).strip())
    except Exception:
        return False


def _discover_repo(extension_root):
    try:
        repo_path = pygit.libgit.Repository.Discover(extension_root)
    except Exception:
        return None
    if not repo_path:
        return None
    try:
        return pygit.get_repo(repo_path)
    except Exception:
        return None


def _git_cli_available():
    try:
        completed = subprocess.Popen(
            ["git", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        completed.communicate()
        return completed.returncode == 0
    except Exception:
        return False


def _run_git(repo_root, args):
    completed = subprocess.Popen(
        ["git", "-C", repo_root] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout, stderr = completed.communicate()
    if completed.returncode != 0:
        raise Exception((stderr or stdout or b"Git command failed.").decode("utf-8", "ignore").strip())


def _git_output(repo_root, args):
    completed = subprocess.Popen(
        ["git", "-C", repo_root] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout, stderr = completed.communicate()
    if completed.returncode != 0:
        raise Exception((stderr or stdout or b"Git command failed.").decode("utf-8", "ignore").strip())
    return (stdout or b"").decode("utf-8", "ignore")


def _sync_to_github(repo_root, target_branch):
    if not _git_cli_available():
        raise Exception(
            "Git CLI is required to update WWPTools.\n\n"
            "Close Revit, install Git, then run Update WWPTools again."
        )
    _run_git(repo_root, ["fetch", "origin", target_branch])
    _run_git(repo_root, ["reset", "--hard", _remote_branch(target_branch)])
    _run_git(repo_root, ["clean", "-ffdx"])
    return pygit.get_repo(repo_root)


def _is_revit_locked_update_error(error):
    err = str(error or "").lower()
    if ".dll" not in err and "wwptools.wpfui" not in err:
        return False
    markers = [
        "unable to unlink",
        "permission denied",
        "access is denied",
        "being used by another process",
        "not uptodate",
        "cannot merge",
        "could not reset index file",
    ]
    for marker in markers:
        if marker in err:
            return True
    return False


def _ensure_target_branch(repo_info, repo_root, target_branch):
    if repo_info.branch == target_branch:
        return repo_info

    # Don't block on dirty files - we always reset --hard so local changes are discarded anyway.
    repo = repo_info.repo
    try:
        local_branch = repo.Branches[target_branch]
    except Exception:
        local_branch = None

    if local_branch is not None:
        try:
            pygit.libgit.Commands.Checkout(repo, local_branch)
            return pygit.get_repo(repo_root)
        except Exception:
            pass

    if not _git_cli_available():
        raise Exception(
            "Installed repo is on branch '{}', but this updater is configured to use '{}'.\n\n"
            "Git CLI is not available to switch branches automatically.".format(
                repo_info.branch,
                _remote_branch(target_branch),
            )
        )

    _run_git(repo_root, ["fetch", "origin", target_branch])
    _run_git(repo_root, ["checkout", "-B", target_branch, _remote_branch(target_branch)])
    return pygit.get_repo(repo_root)


class _DivergenceResult(object):
    def __init__(self, behind, ahead):
        self.BehindBy = behind
        self.AheadBy = ahead


def _history_divergence(repo_info, repo_root, target_branch):
    try:
        pygit.git_fetch(repo_info)
        return pygit.compare_branch_heads(repo_info)
    except Exception:
        pass
    if not _git_cli_available():
        return None
    try:
        _run_git(repo_root, ["fetch", "origin", target_branch])
        behind = int(_git_output(repo_root, ["rev-list", "--count", "HEAD..{}".format(_remote_branch(target_branch))]).strip())
        ahead  = int(_git_output(repo_root, ["rev-list", "--count", "{}..HEAD".format(_remote_branch(target_branch))]).strip())
        return _DivergenceResult(behind, ahead)
    except Exception:
        return None


def _open_latest_release():
    script.open_url(RELEASES_URL)


def _show_not_repo_message():
    open_release = _confirm(
        "This installation is not a Git clone, so WWPTools can not update it with built-in Git.\n\n"
        "Future installs and updates should be done through pyRevit Extension Manager using:\n"
        "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools\n\n"
        "Open the latest GitHub release?",
        TITLE,
    )
    if open_release:
        _open_latest_release()


# ---------------------------------------------------------------------------
# Main update flow
# ---------------------------------------------------------------------------

def _available_update_branches(repo_root, current_branch):
    branches = []
    if current_branch:
        branches.append(current_branch)
    for branch in SUPPORTED_UPDATE_BRANCHES:
        if branch not in branches:
            branches.append(branch)

    if _git_cli_available():
        try:
            _run_git(repo_root, ["fetch", "origin", "--prune"])
            output = _git_output(
                repo_root,
                ["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"]
            )
            remote_branches = []
            for line in output.splitlines():
                value = line.strip()
                if not value or value == "origin/HEAD" or not value.startswith("origin/"):
                    continue
                remote_branches.append(value.split("/", 1)[1])
            branches = [branch for branch in branches if branch in remote_branches]
            for branch in remote_branches:
                if branch in SUPPORTED_UPDATE_BRANCHES and branch not in branches:
                    branches.append(branch)
        except Exception:
            pass
    return branches or list(SUPPORTED_UPDATE_BRANCHES)


def _select_update_branch(repo_info, repo_root):
    current_branch = str(getattr(repo_info, "branch", "") or "").strip()
    branches = _available_update_branches(repo_root, current_branch)
    labels = []
    label_to_branch = {}
    for branch in branches:
        label = branch
        if branch == current_branch:
            label += "  (current)"
        elif branch == "pyrevit-6.1":
            label += "  (pyRevit 6.1 stable)"
        elif branch == "pyrevit-6.4":
            label += "  (pyRevit 6.4+)"
        labels.append(label)
        label_to_branch[label] = branch

    try:
        from pyrevit import forms  # type: ignore
        selected = forms.SelectFromList.show(
            labels,
            title="Select WWPTools Update Branch",
            button_name="Use Selected Branch",
            multiselect=False,
        )
        if not selected:
            return None
        return label_to_branch.get(selected, selected.split()[0])
    except Exception:
        if current_branch:
            return current_branch
        return branches[0] if branches else "main"


def _update_repo(repo_info, repo_root):
    target_branch = _select_update_branch(repo_info, repo_root)
    if not target_branch:
        return
    repo_info = _ensure_target_branch(repo_info, repo_root, target_branch)
    if repo_info is None:
        return
    divergence = _history_divergence(repo_info, repo_root, target_branch)
    behind = int(divergence.BehindBy) if divergence and divergence.BehindBy is not None else 0
    ahead  = int(divergence.AheadBy)  if divergence and divergence.AheadBy  is not None else 0
    dirty = _working_tree_dirty(repo_root)

    current_tag = _latest_tag(repo_root)
    current_label = "{} ({})".format(current_tag, repo_info.last_commit_hash[:7]) if current_tag \
        else repo_info.last_commit_hash[:7]

    if behind <= 0 and ahead <= 0 and not dirty:
        msg = "WWPTools is already up to date.\n\nVersion: {}\nBranch: {}".format(
            current_label, repo_info.branch,
        )
        _alert(msg, TITLE)
        return

    remote_tag = _remote_tag(repo_root, target_branch)
    remote_label = "{} ({})".format(remote_tag, "incoming") if remote_tag else "{} commit(s)".format(behind)
    changelog = _incoming_log(repo_root, target_branch)
    changed_files = _incoming_changed_files(repo_root, target_branch)
    needs_revit_close = _update_needs_revit_close(changed_files)

    confirm_msg = (
        "Updates are available for WWPTools.\n\n"
        "Current version:  {}\n"
        "New version:      {}\n"
        "Update branch:    {}\n\n"
        "What's new:\n{}\n\n"
        "Update behavior:\n"
        "Local WWPTools files will be overwritten with GitHub files.\n"
        "Local changes will not be committed or kept.\n"
        "Files not in GitHub will be deleted locally.\n\n"
        "Update now?"
    ).format(
        current_label,
        remote_label,
        target_branch,
        changelog if changelog else "  (commit log unavailable)",
    )
    if needs_revit_close:
        confirm_msg = (
            confirm_msg +
            "\n\nThis update includes DLL files that Revit has loaded.\n"
            "Close Revit first, then run Update WWPTools again to apply the update."
        )
    if not _confirm(confirm_msg, TITLE):
        return

    if needs_revit_close:
        _alert(
            "This update includes DLL files that Revit can not replace while it is running.\n\n"
            "Please close Revit completely, then run Update WWPTools again.",
            TITLE,
        )
        return

    try:
        updated_repo = _sync_to_github(repo_root, target_branch)
    except Exception as sync_err:
        if _is_revit_locked_update_error(sync_err):
            _alert(
                "A WWPTools DLL is locked by Revit.\n\n"
                "Please close Revit completely, then run Update WWPTools again.",
                TITLE,
            )
            return
        raise

    after_hash = updated_repo.last_commit_hash[:7]
    new_tag = _latest_tag(repo_root)
    after_label = "{} ({})".format(new_tag, after_hash) if new_tag else after_hash

    reload_offered = _confirm(
        "WWPTools updated successfully.\n\n"
        "Previous version: {}\n"
        "New version:      {}\n\n"
        "Reload pyRevit now to apply the changes?\n"
        "(Choosing No means you'll need to restart Revit manually.)".format(
            current_label,
            after_label,
        ),
        TITLE,
    )
    if reload_offered:
        if not _reload_pyrevit():
            _alert(
                "Could not reload pyRevit automatically.\n\nPlease restart Revit to apply the update.",
                TITLE,
            )


def main():
    repo_root = _extension_root()
    repo_info = _discover_repo(repo_root)
    if not repo_info:
        _show_not_repo_message()
        return
    _update_repo(repo_info, repo_root)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _alert(traceback.format_exc(), TITLE)
