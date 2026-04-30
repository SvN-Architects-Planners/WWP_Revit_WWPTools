import os
import subprocess
import sys
import tempfile
import traceback

from pyrevit import script
from pyrevit.coreutils import git as pygit


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)


TITLE = "Update WWPTools"
RELEASES_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest"
TARGET_BRANCH = "main"
TARGET_REMOTE_BRANCH = "origin/main"

# Windows process-creation flags (safe fallback for IronPython)
_DETACHED_PROCESS       = getattr(subprocess, "DETACHED_PROCESS",       0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)


def _revit_ui():
    try:
        import clr
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit import UI
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
        from pyrevit import forms
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
        from pyrevit import forms
        return bool(forms.alert(text, title=caption or TITLE, yes=True, no=True))
    except Exception:
        return False


def _reload_pyrevit():
    try:
        from pyrevit.loader import sessionmgr
        sessionmgr.reload_pyrevit()
        return True
    except Exception:
        pass
    try:
        from pyrevit.loader import sessionmgr as sm
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


def _remote_tag(repo_root):
    if not _git_cli_available():
        return None
    try:
        tag = _git_output(
            repo_root,
            ["describe", "--tags", "--abbrev=0", "origin/{}".format(TARGET_BRANCH)]
        ).strip()
        return tag if tag else None
    except Exception:
        return None


def _incoming_log(repo_root, max_lines=10):
    if not _git_cli_available():
        return ""
    try:
        log = _git_output(
            repo_root,
            ["log", "--oneline", "HEAD..origin/{}".format(TARGET_BRANCH)]
        ).strip()
        lines = log.splitlines()
        if len(lines) > max_lines:
            lines = lines[:max_lines] + ["... and {} more".format(len(lines) - max_lines)]
        return "\n".join(lines)
    except Exception:
        return ""


def _incoming_changed_files(repo_root):
    if not _git_cli_available():
        return []
    try:
        output = _git_output(
            repo_root,
            ["diff", "--name-only", "HEAD..origin/{}".format(TARGET_BRANCH)]
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


def _sync_to_github(repo_root):
    if not _git_cli_available():
        raise Exception(
            "Git CLI is required to update WWPTools.\n\n"
            "Close Revit, install Git, then run Update WWPTools again."
        )
    _run_git(repo_root, ["fetch", "origin", TARGET_BRANCH])
    _run_git(repo_root, ["reset", "--hard", TARGET_REMOTE_BRANCH])
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



def _ensure_target_branch(repo_info, repo_root):
    if repo_info.branch == TARGET_BRANCH:
        return repo_info

    # Don't block on dirty files - we always reset --hard so local changes are discarded anyway.
    repo = repo_info.repo
    try:
        target_branch = repo.Branches[TARGET_BRANCH]
    except Exception:
        target_branch = None

    if target_branch is not None:
        try:
            pygit.libgit.Commands.Checkout(repo, target_branch)
            return pygit.get_repo(repo_root)
        except Exception:
            pass

    if not _git_cli_available():
        raise Exception(
            "Installed repo is on branch '{}', but this updater is configured to use '{}'.\n\n"
            "Git CLI is not available to switch branches automatically.".format(
                repo_info.branch,
                TARGET_REMOTE_BRANCH,
            )
        )

    _run_git(repo_root, ["fetch", "origin", TARGET_BRANCH])
    _run_git(repo_root, ["checkout", "-B", TARGET_BRANCH, TARGET_REMOTE_BRANCH])
    return pygit.get_repo(repo_root)


class _DivergenceResult(object):
    def __init__(self, behind, ahead):
        self.BehindBy = behind
        self.AheadBy = ahead


def _history_divergence(repo_info, repo_root):
    try:
        pygit.git_fetch(repo_info)
        return pygit.compare_branch_heads(repo_info)
    except Exception:
        pass
    if not _git_cli_available():
        return None
    try:
        _run_git(repo_root, ["fetch", "origin", TARGET_BRANCH])
        behind = int(_git_output(repo_root, ["rev-list", "--count", "HEAD..origin/{}".format(TARGET_BRANCH)]).strip())
        ahead  = int(_git_output(repo_root, ["rev-list", "--count", "origin/{}..HEAD".format(TARGET_BRANCH)]).strip())
        return _DivergenceResult(behind, ahead)
    except Exception:
        return None


def _open_latest_release():
    script.open_url(RELEASES_URL)


def _show_not_repo_message():
    open_release = _confirm(
        "This installation is not a Git clone, so WWPTools can not update it with built-in Git.\n\n"
        "Open the latest GitHub release instead?",
        TITLE,
    )
    if open_release:
        _open_latest_release()


# ---------------------------------------------------------------------------
# Background updater - runs after Revit exits
# ---------------------------------------------------------------------------

def _standby_log_path(repo_root, pid):
    base_dir = os.environ.get("LOCALAPPDATA")
    if not base_dir:
        base_dir = tempfile.gettempdir()
    log_dir = os.path.normpath(os.path.join(base_dir, "WWPTools", "UpdateLogs"))
    try:
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
    except Exception:
        log_dir = tempfile.gettempdir()
    return os.path.normpath(os.path.join(log_dir, "wwptools_update_{}.log".format(pid)))

def _schedule_update_on_revit_exit(repo_root, installed_label=None):
    """
    Spawn a detached cmd process that waits until Revit fully closes,
    then resets to origin/main so locked DLLs are no longer an obstacle.
    Returns True if the watcher was started successfully.
    """
    pid = os.getpid()
    safe_root = os.path.normpath(repo_root)
    log_path = _standby_log_path(repo_root, pid)

    start_notice = _powershell_message_command(
        TITLE,
        "Revit has closed. WWPTools update is now running in the background. "
        "You will get a completion message when it finishes.",
    )
    installed_text = installed_label or "the latest available version"
    success_notice = _powershell_message_command(
        TITLE,
        "WWPTools update finished successfully to {}. You can reopen Revit now.\n\n"
        "Update finished successfully after Revit closed.\n\n"
        "Log file:\n{}".format(installed_text, log_path),
    )
    failed_notice = _powershell_message_command(
        TITLE,
        "WWPTools update failed after Revit closed.\n\n"
        "Check the update log for details:\n{}".format(log_path),
    )

    # Batch script: poll until all Revit processes are gone, then mirror origin/main.
    batch = "\r\n".join([
        "@echo off",
        "setlocal",
        "set \"LOG={log}\"".format(log=log_path),
        "echo [%%date%% %%time%%] Standby updater started. > \"%%LOG%%\"",
        "echo [%%date%% %%time%%] Waiting for Revit.exe to close... >> \"%%LOG%%\"",
        ":wait",
        "tasklist /FI \"IMAGENAME eq Revit.exe\" 2>NUL | find /I \"Revit.exe\" >NUL",
        "if not errorlevel 1 (timeout /t 3 /nobreak >NUL & goto wait)",
        "echo [%%date%% %%time%%] Revit closed. Starting Git sync... >> \"%%LOG%%\"",
        start_notice,
        "git -C \"{root}\" fetch origin {branch} >> \"%%LOG%%\" 2>&1".format(root=safe_root, branch=TARGET_BRANCH),
        "if errorlevel 1 goto failed",
        "git -C \"{root}\" reset --hard origin/{branch} >> \"%%LOG%%\" 2>&1".format(root=safe_root, branch=TARGET_BRANCH),
        "if errorlevel 1 goto failed",
        "git -C \"{root}\" clean -ffdx >> \"%%LOG%%\" 2>&1".format(root=safe_root),
        "if errorlevel 1 goto failed",
        "echo [%%date%% %%time%%] Update completed successfully. >> \"%%LOG%%\"",
        success_notice,
        "exit /b 0",
        ":failed",
        "echo [%%date%% %%time%%] Update failed. >> \"%%LOG%%\"",
        failed_notice,
        "exit /b 1",
    ]) + "\r\n"

    batch_path = os.path.join(
        tempfile.gettempdir(),
        "wwptools_update_{}.bat".format(pid),
    )
    try:
        with open(batch_path, "w") as f:
            f.write(batch)
        subprocess.Popen(
            ["cmd", "/c", batch_path],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return True
    except Exception:
        return False


def _start_standby_update(repo_root, reason, installed_label=None):
    if not _git_cli_available():
        _alert(
            "WWPTools can not finish this update while Revit is open.\n\n"
            "{}\n\n"
            "Git CLI is not available to run the standby updater.\n"
            "Close Revit completely, then run Update WWPTools again.".format(reason),
            TITLE,
        )
        return
    if _schedule_update_on_revit_exit(repo_root, installed_label=installed_label):
        _alert(
            "WWPTools is ready to finish updating, but Revit is using one or more files.\n\n"
            "{}\n\n"
            "Close all Revit windows. The standby updater will finish automatically after Revit closes.\n"
            "You can reopen Revit after the update finishes.".format(reason),
            TITLE,
        )
    else:
        _alert(
            "Could not start the standby updater.\n\n"
            "Close Revit completely, then run Update WWPTools again.",
            TITLE,
        )


# ---------------------------------------------------------------------------
# Main update flow
# ---------------------------------------------------------------------------

def _update_repo(repo_info, repo_root):
    repo_info = _ensure_target_branch(repo_info, repo_root)
    if repo_info is None:
        return
    divergence = _history_divergence(repo_info, repo_root)
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

    remote_tag = _remote_tag(repo_root)
    remote_label = "{} ({})".format(remote_tag, "incoming") if remote_tag else "{} commit(s)".format(behind)
    changelog = _incoming_log(repo_root)
    changed_files = _incoming_changed_files(repo_root)
    needs_revit_close = _update_needs_revit_close(changed_files)

    confirm_msg = (
        "Updates are available for WWPTools.\n\n"
        "Current version:  {}\n"
        "New version:      {}\n"
        "Branch:           {}\n\n"
        "What's new:\n{}\n\n"
        "Update behavior:\n"
        "Local WWPTools files will be overwritten with GitHub files.\n"
        "Local changes will not be committed or kept.\n"
        "Files not in GitHub will be deleted locally.\n\n"
        "Update now?"
    ).format(
        current_label,
        remote_label,
        repo_info.branch,
        changelog if changelog else "  (commit log unavailable)",
    )
    if needs_revit_close:
        confirm_msg = (
            confirm_msg +
            "\n\nThis update includes DLL files that Revit may have loaded.\n"
            "WWPTools will start a standby updater and finish after you close all Revit windows."
        )
    if not _confirm(confirm_msg, TITLE):
        return

    if needs_revit_close:
        _start_standby_update(
            repo_root,
            "This update includes DLL files that Revit can not replace while it is running.",
            installed_label=remote_tag,
        )
        return

    dll_locked = False
    try:
        updated_repo = _sync_to_github(repo_root)
    except Exception as sync_err:
        if _is_revit_locked_update_error(sync_err):
            dll_locked = True
        else:
            raise

    if dll_locked:
        _start_standby_update(
            repo_root,
            "A WWPTools DLL is locked by Revit, so Git can not replace it yet.",
            installed_label=remote_tag,
        )
        return

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
