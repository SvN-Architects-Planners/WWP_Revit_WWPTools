__context__ = "zero-doc"

import os
import subprocess
import sys
import tempfile
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
# Deferred update (for DLL-locked updates that require Revit to be closed)
# ---------------------------------------------------------------------------

def _github_archive_url(repo_root, target_branch):
    """Derive the GitHub archive zip URL from the git remote, or fall back to RELEASES_URL."""
    raw = ""
    if _git_cli_available():
        try:
            raw = _git_output(repo_root, ["remote", "get-url", "origin"]).strip()
        except Exception:
            pass
    if not raw:
        base = RELEASES_URL.rsplit("/releases", 1)[0] if "/releases" in RELEASES_URL else None
        return "{}/archive/refs/heads/{}.zip".format(base, target_branch) if base else None
    url = raw.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    return "{}/archive/refs/heads/{}.zip".format(url, target_branch)


def _incoming_modified_non_dll_files(repo_root, target_branch):
    """Return files that are MODIFIED (not added/deleted/renamed) in the incoming
    commits, excluding DLLs. These can be updated in-place without closing Revit."""
    if not _git_cli_available():
        return []
    try:
        output = _git_output(
            repo_root,
            ["diff", "--name-status", "HEAD..origin/{}".format(target_branch)]
        )
        result = []
        for line in output.splitlines():
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            status = parts[0].strip().upper()
            path = parts[1].strip().replace("\\", "/")
            if status == "M" and not path.lower().endswith(".dll"):
                result.append(path)
        return result
    except Exception:
        return []


def _partial_update_non_dll(repo_root, target_branch, files):
    """Check out modified non-DLL files from origin in-place (no Revit close needed).
    Returns the count of successfully updated files."""
    if not files or not _git_cli_available():
        return 0
    remote = "origin/{}".format(target_branch)
    updated = 0
    for f in files:
        try:
            _run_git(repo_root, ["checkout", remote, "--", f])
            updated += 1
        except Exception:
            pass
    return updated


def _write_deferred_update_bat(repo_root, target_branch):
    """Write a self-contained .bat that applies the full update after Revit is closed.

    Primary path: uses git CLI (fetch + reset --hard + clean) — proper update, HEAD advances.
    Fallback (no git CLI): PowerShell downloads the GitHub archive zip and copies only the
    DLL files. The .git HEAD stays at the old commit, but the DLLs are correct; the NEXT
    run of Update WWPTools will see 0 commits behind (or reconcile cleanly via reset --hard).

    Returns the path to the written .bat, or None on failure.
    """
    base_dir = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    out_dir = os.path.normpath(os.path.join(base_dir, "WWPTools", "PendingUpdates"))
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except Exception:
        out_dir = tempfile.gettempdir()

    bat_path = os.path.normpath(os.path.join(out_dir, "UpdateWWPTools.bat"))
    repo_norm = os.path.normpath(repo_root)
    remote    = "origin/{}".format(target_branch)
    zip_url   = _github_archive_url(repo_root, target_branch) or ""
    lib_dst   = os.path.join(repo_norm, "WWPTools.extension", "lib")

    # PowerShell command: download zip, copy DLLs only, note about re-running Update
    if zip_url:
        ps_fallback = (
            "powershell -NoProfile -ExecutionPolicy Bypass -Command \""
            "$url = '{zip}'; "
            "$tmp = [IO.Path]::Combine($env:TEMP, 'wwptools_update.zip'); "
            "$dst = [IO.Path]::Combine($env:TEMP, 'wwptools_extracted'); "
            "try {{ "
            "  Write-Host '  Downloading from GitHub...'; "
            "  Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing; "
            "  if (Test-Path $dst) {{ Remove-Item $dst -Recurse -Force }}; "
            "  Expand-Archive $tmp -DestinationPath $dst -Force; "
            "  $src = (Get-ChildItem $dst -Directory | Select-Object -First 1).FullName; "
            "  Copy-Item \"$src\\WWPTools.extension\\lib\\*.dll\" '{lib}\\' -Force; "
            "  Remove-Item $tmp, $dst -Recurse -Force -ErrorAction SilentlyContinue; "
            "  Write-Host '  DLLs copied from GitHub archive.'; "
            "  Write-Host '  NOTE: Run Update WWPTools once more inside Revit to sync the git record.'; "
            "}} catch {{ "
            "  Write-Host ('  Download failed: ' + $_.Exception.Message); exit 1 "
            "}}\""
        ).format(
            zip=zip_url.replace("'", "''"),
            lib=lib_dst.replace("\\", "\\\\").replace("'", "''"),
        )
    else:
        ps_fallback = (
            "echo  Cannot determine download URL."
            " Install Git from git-scm.com and re-run this script."
            " && goto :fail"
        )

    lines = [
        "@echo off",
        "setlocal",
        "echo.",
        "echo  WWPTools Update",
        "echo  ================",
        "echo.",
        ":: Check whether git is available on PATH",
        "git --version >nul 2>&1",
        "if errorlevel 1 goto :nogit",
        "",
        ":: ── git path: full fetch + reset + clean ────────────────────────────",
        "echo  Updating with git...",
        'git -C "{repo}" fetch origin {branch}'.format(repo=repo_norm, branch=target_branch),
        "if errorlevel 1 goto :fail",
        'git -C "{repo}" reset --hard {remote}'.format(repo=repo_norm, remote=remote),
        "if errorlevel 1 goto :fail",
        'git -C "{repo}" clean -ffdx'.format(repo=repo_norm),
        "if errorlevel 1 goto :fail",
        "goto :success",
        "",
        ":: ── no-git path: PowerShell zip download (DLLs only) ───────────────",
        ":nogit",
        "echo  Git not found on PATH. Using PowerShell download (DLLs only)...",
        ps_fallback,
        "if errorlevel 1 goto :fail",
        "goto :success",
        "",
        ":success",
        "echo.",
        "echo  WWPTools updated successfully.",
        "echo  Start Revit to use the new version.",
        "echo.",
        "goto :done",
        "",
        ":fail",
        "echo.",
        "echo  Update FAILED. See the error above.",
        "echo.",
        "",
        ":done",
        "pause",
        "(goto) 2>nul & del /f /q \"%~f0\"",
    ]

    try:
        with open(bat_path, "w") as fh:
            fh.write("\r\n".join(lines) + "\r\n")
        return bat_path
    except Exception:
        return None


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
            "\n\nThis update includes DLL files that require Revit to be closed.\n"
            "Python/config files will be updated now. A one-click script will be\n"
            "prepared for the DLL update — run it after closing Revit."
        )
    if not _confirm(confirm_msg, TITLE):
        return

    if needs_revit_close:
        # Step 1 — update Python/config files in-place now (no Revit close needed).
        # Only M (modified) non-DLL files are safe to update without a full reset.
        modified_non_dll = _incoming_modified_non_dll_files(repo_root, target_branch)
        python_updated   = _partial_update_non_dll(repo_root, target_branch, modified_non_dll)

        # Step 2 — write deferred bat for the DLL update.
        bat_path = _write_deferred_update_bat(repo_root, target_branch)

        python_note = (
            "{} Python/config file(s) updated now.\n\n".format(python_updated)
            if python_updated > 0 else ""
        )

        if bat_path:
            _alert(
                "{}DLL files require Revit to be closed before they can be replaced.\n\n"
                "A one-click update script has been prepared:\n"
                "{}\n\n"
                "1. Close Revit completely.\n"
                "2. Double-click the script to apply the DLL update.\n\n"
                "The script deletes itself after running.\n"
                "(No git CLI? The script will download the DLLs from GitHub instead.)".format(
                    python_note, bat_path),
                TITLE,
            )
            try:
                subprocess.Popen(
                    ["explorer", "/select,", bat_path],
                    shell=False,
                    creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
                )
            except Exception:
                pass
        else:
            _alert(
                "{}DLL files require Revit to be closed before they can be replaced.\n\n"
                "Please close Revit completely, then run Update WWPTools again.".format(python_note),
                TITLE,
            )

        # Step 3 — if Python files were updated, offer a pyRevit reload now.
        if python_updated > 0:
            if _confirm(
                "{} Python/config file(s) updated successfully.\n\n"
                "Reload pyRevit now to activate the changes?\n"
                "(Choose No if you want to restart Revit manually later.)".format(python_updated),
                TITLE,
            ):
                if not _reload_pyrevit():
                    _alert(
                        "Could not reload pyRevit automatically.\n\nPlease restart Revit.",
                        TITLE,
                    )
        return

    try:
        updated_repo = _sync_to_github(repo_root, target_branch)
    except Exception as sync_err:
        if _is_revit_locked_update_error(sync_err):
            modified_non_dll = _incoming_modified_non_dll_files(repo_root, target_branch)
            python_updated   = _partial_update_non_dll(repo_root, target_branch, modified_non_dll)
            bat_path = _write_deferred_update_bat(repo_root, target_branch)
            python_note = (
                "{} Python/config file(s) updated now.\n\n".format(python_updated)
                if python_updated > 0 else ""
            )
            if bat_path:
                _alert(
                    "{}A WWPTools DLL is locked by Revit.\n\n"
                    "A one-click update script has been prepared:\n"
                    "{}\n\n"
                    "1. Close Revit completely.\n"
                    "2. Double-click the script to finish the DLL update.".format(
                        python_note, bat_path),
                    TITLE,
                )
                try:
                    subprocess.Popen(
                        ["explorer", "/select,", bat_path],
                        shell=False,
                        creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
                    )
                except Exception:
                    pass
            else:
                _alert(
                    "{}A WWPTools DLL is locked by Revit.\n\n"
                    "Please close Revit completely, then run Update WWPTools again.".format(
                        python_note),
                    TITLE,
                )
            if python_updated > 0:
                if _confirm(
                    "{} Python/config file(s) updated. Reload pyRevit now?".format(python_updated),
                    TITLE,
                ):
                    _reload_pyrevit()
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
