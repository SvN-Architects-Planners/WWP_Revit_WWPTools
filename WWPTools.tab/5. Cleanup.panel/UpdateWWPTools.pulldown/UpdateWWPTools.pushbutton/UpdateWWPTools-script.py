__context__ = "zero-doc"

import os
import shutil
import subprocess
import sys
import tempfile
import traceback

from pyrevit import script  # type: ignore
from pyrevit.coreutils import git as pygit  # type: ignore


script_dir = os.path.dirname(__file__)


def _find_extension_root(start_dir):
    path = os.path.abspath(start_dir)
    while path and path != os.path.dirname(path):
        if path.lower().endswith(".extension"):
            return path
        path = os.path.dirname(path)
    return os.path.abspath(os.path.join(start_dir, "..", "..", ".."))


EXTENSION_ROOT = _find_extension_root(script_dir)
MANUAL_GENERATE_UPDATER = bool(globals().get("MANUAL_GENERATE_UPDATER", False))
FORCE_GENERATE_UPDATER = bool(globals().get("FORCE_GENERATE_UPDATER", False))

lib_path = os.path.abspath(os.path.join(EXTENSION_ROOT, "lib"))
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)


TITLE = "Update WWPTools"
RELEASES_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools/releases/latest"
REPO_URL = "https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools.git"
DEFAULT_UPDATE_BRANCH = "main"
SUPPORTED_UPDATE_BRANCHES = ("main", "pyrevit-6.1", "pyrevit-6.4")

# Windows process-creation flags (safe fallback for IronPython)
_DETACHED_PROCESS         = getattr(subprocess, "DETACHED_PROCESS",         0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_NEW_CONSOLE       = 0x00000010  # opens a visible console window for the child process


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
    return os.path.normpath(EXTENSION_ROOT)


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


def _incoming_name_status(repo_root, target_branch):
    if not _git_cli_available():
        return []
    try:
        output = _git_output(
            repo_root,
            ["diff", "--name-status", "HEAD..{}".format(_remote_branch(target_branch))]
        ).strip()
        changes = []
        for line in output.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                parts = line.split(None)
            if len(parts) < 2:
                continue
            status = parts[0].strip().upper()
            paths = [p.strip().replace("\\", "/") for p in parts[1:] if p.strip()]
            changes.append((status, paths))
        return changes
    except Exception:
        return []


def _path_is_dll(path):
    return str(path or "").lower().endswith(".dll")


def _classify_incoming_changes(repo_root, target_branch):
    changes = _incoming_name_status(repo_root, target_branch)
    has_dll = False
    has_structural = False
    modified_non_dll = []

    for status, paths in changes:
        status_code = status[:1]
        if any(_path_is_dll(path) for path in paths):
            has_dll = True
        if status_code == "M" and len(paths) == 1:
            if not _path_is_dll(paths[0]):
                modified_non_dll.append(paths[0])
            continue
        has_structural = True

    return {
        "changes": changes,
        "has_dll": has_dll,
        "has_structural": has_structural,
        "modified_non_dll": modified_non_dll,
    }


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
        raise Exception("Git CLI is not available.")
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
    update_now = _confirm(
        "This installation is not a Git clone.\n\n"
        "WWPTools can still update by downloading the latest GitHub ZIP after Revit closes.\n\n"
        "Prepare that update now?",
        TITLE,
    )
    if update_now:
        _prepare_full_zip_update(_extension_root(), DEFAULT_UPDATE_BRANCH)


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


def _write_full_zip_update_bat(extension_root, target_branch):
    """Write a .bat that replaces the full extension folder from GitHub after Revit closes."""
    base_dir = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    out_dir = os.path.normpath(os.path.join(base_dir, "WWPTools", "PendingUpdates"))
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except Exception:
        out_dir = tempfile.gettempdir()

    bat_path = os.path.normpath(os.path.join(out_dir, "UpdateWWPTools-Zip.bat"))
    extension_norm = os.path.normpath(extension_root)
    zip_url = _github_archive_url(extension_root, target_branch) or ""

    if not zip_url:
        return None

    ps_update = (
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \""
        "$ErrorActionPreference = 'Stop'; "
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        "$url = '{zip}'; "
        "$ext = '{ext}'; "
        "$tmpRoot = Join-Path $env:TEMP ('WWPTools_Update_' + [guid]::NewGuid().ToString('N')); "
        "$zipPath = Join-Path $tmpRoot 'wwptools.zip'; "
        "$extract = Join-Path $tmpRoot 'extract'; "
        "New-Item -ItemType Directory -Force -Path $tmpRoot,$extract | Out-Null; "
        "try {{ "
        "  Write-Host '  Downloading from GitHub...'; "
        "  Invoke-WebRequest -Uri $url -OutFile $zipPath; "
        "  Expand-Archive -LiteralPath $zipPath -DestinationPath $extract -Force; "
        "  $src = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1; "
        "  if (-not $src) {{ throw 'Downloaded ZIP did not contain a folder.' }}; "
        "  if (Test-Path -LiteralPath $ext) {{ Remove-Item -LiteralPath $ext -Recurse -Force }}; "
        "  Move-Item -LiteralPath $src.FullName -Destination $ext; "
        "  if (-not (Test-Path -LiteralPath $ext -PathType Container)) {{ throw 'Extension folder was not created.' }}; "
        "  Write-Host '  Extension folder replaced from GitHub ZIP.'; "
        "}} finally {{ "
        "  if (Test-Path -LiteralPath $tmpRoot) {{ Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue }} "
        "}}\""
    ).format(
        zip=zip_url.replace("'", "''"),
        ext=extension_norm.replace("'", "''"),
    )

    lines = [
        "@echo off",
        "setlocal",
        "title WWPTools Update",
        "echo.",
        "echo  WWPTools Update",
        "echo  ================",
        "echo.",
        ":waitrevit",
        'tasklist /FI "IMAGENAME eq Revit.exe" 2>nul | find /I "Revit.exe" >nul 2>&1',
        "if not errorlevel 1 (",
        "  echo  Revit is still running.",
        "  echo.",
        "  echo  Close all Revit windows, then press any key to check again.",
        "  pause >nul",
        "  cls",
        "  echo.",
        "  echo  WWPTools Update",
        "  echo  ================",
        "  echo.",
        "  goto :waitrevit",
        ")",
        "echo  Revit has closed. Starting ZIP update...",
        "echo.",
        ps_update,
        "if errorlevel 1 goto :fail",
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


def _prepare_full_zip_update(extension_root, target_branch):
    bat_path = _write_full_zip_update_bat(extension_root, target_branch)
    if not bat_path:
        _alert(
            "Could not prepare the ZIP updater.\n\n"
            "Open the latest GitHub release and install WWPTools manually.",
            TITLE,
        )
        _open_latest_release()
        return

    launched = _launch_bat_in_console(bat_path)
    if launched:
        _alert(
            "A console window is now waiting for Revit to close.\n\n"
            "Close Revit - WWPTools will update from the latest GitHub ZIP automatically.\n\n"
            "Script location (if the window was blocked):\n"
            "{}".format(bat_path),
            TITLE,
        )
    else:
        _alert(
            "Close Revit, then double-click this update script:\n"
            "{}".format(bat_path),
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


def _write_deferred_update_bat(repo_root, target_branch):
    """Write a self-contained .bat that applies the full update after Revit is closed.

    Primary path: uses git CLI (fetch + reset --hard + clean) - proper update, HEAD advances.
    Fallback (no git CLI): PowerShell downloads the GitHub archive zip and replaces the
    full extension folder.

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

    # PowerShell command: download zip and replace the full extension folder.
    if zip_url:
        ps_fallback = (
            "powershell -NoProfile -ExecutionPolicy Bypass -Command \""
            "$ErrorActionPreference = 'Stop'; "
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
            "$url = '{zip}'; "
            "$ext = '{ext}'; "
            "$tmpRoot = Join-Path $env:TEMP ('WWPTools_Update_' + [guid]::NewGuid().ToString('N')); "
            "$zipPath = Join-Path $tmpRoot 'wwptools.zip'; "
            "$extract = Join-Path $tmpRoot 'extract'; "
            "New-Item -ItemType Directory -Force -Path $tmpRoot,$extract | Out-Null; "
            "try {{ "
            "  Write-Host '  Downloading from GitHub...'; "
            "  Invoke-WebRequest -Uri $url -OutFile $zipPath; "
            "  Expand-Archive -LiteralPath $zipPath -DestinationPath $extract -Force; "
            "  $src = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1; "
            "  if (-not $src) {{ throw 'Downloaded ZIP did not contain a folder.' }}; "
            "  if (Test-Path -LiteralPath $ext) {{ Remove-Item -LiteralPath $ext -Recurse -Force }}; "
            "  Move-Item -LiteralPath $src.FullName -Destination $ext; "
            "  if (-not (Test-Path -LiteralPath $ext -PathType Container)) {{ throw 'Extension folder was not created.' }}; "
            "  Write-Host '  Extension folder replaced from GitHub ZIP.'; "
            "}} catch {{ "
            "  Write-Host ('  Download failed: ' + $_.Exception.Message); exit 1 "
            "}} finally {{ "
            "  if (Test-Path -LiteralPath $tmpRoot) {{ Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue }} "
            "}}\""
        ).format(
            zip=zip_url.replace("'", "''"),
            ext=repo_norm.replace("'", "''"),
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
        "title WWPTools Update",
        "echo.",
        "echo  WWPTools Update",
        "echo  ================",
        "echo.",
        ":: Wait for all Revit.exe processes to exit before updating",
        ":waitrevit",
        'tasklist /FI "IMAGENAME eq Revit.exe" 2>nul | find /I "Revit.exe" >nul 2>&1',
        "if not errorlevel 1 (",
        "  echo  Revit is still running.",
        "  echo.",
        "  echo  Close all Revit windows, then press any key to check again.",
        "  pause >nul",
        "  cls",
        "  echo.",
        "  echo  WWPTools Update",
        "  echo  ================",
        "  echo.",
        "  goto :waitrevit",
        ")",
        "echo  Revit has closed. Starting update...",
        "echo.",
        ":: Check whether git is available on PATH",
        "git --version >nul 2>&1",
        "if errorlevel 1 goto :nogit",
        "",
        ":: -- git path: full fetch + reset + clean --",
        "echo  Updating with git...",
        'git -C "{repo}" fetch origin {branch}'.format(repo=repo_norm, branch=target_branch),
        "if errorlevel 1 goto :fail",
        'git -C "{repo}" reset --hard {remote}'.format(repo=repo_norm, remote=remote),
        "if errorlevel 1 goto :fail",
        'git -C "{repo}" clean -ffdx'.format(repo=repo_norm),
        "if errorlevel 1 goto :fail",
        "goto :success",
        "",
        ":: -- no-git path: PowerShell zip download (full extension folder) --",
        ":nogit",
        "echo  Git not found on PATH. Using PowerShell ZIP download...",
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


def _force_extension_path():
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        return os.path.normpath(
            os.path.join(appdata, "pyRevit", "Extensions", "WWP_Revit_WWPTools.extension")
        )
    return os.path.normpath(_extension_root())


def _force_pending_dir():
    base_dir = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    out_dir = os.path.normpath(os.path.join(base_dir, "WWPTools", "PendingUpdates"))
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except Exception:
        out_dir = tempfile.gettempdir()
    return out_dir


def _force_temp_clone_path():
    return os.path.normpath(os.path.join(_force_pending_dir(), "ForceClone"))


def _write_text_file(path, text):
    with open(path, "w") as fh:
        fh.write(str(text or ""))


def _delete_file_if_exists(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _prepare_force_git_clone(temp_clone, ready_file, fail_file, status_file):
    """Create a fresh git clone using pyRevit's bundled git support before Revit closes."""
    try:
        _write_text_file(status_file, "Downloading fresh WWPTools git clone from GitHub...")
        if os.path.exists(temp_clone):
            shutil.rmtree(temp_clone)
        pygit.git_clone(REPO_URL, temp_clone)
        _write_text_file(status_file, "Verifying prepared git clone...")
        if not os.path.isdir(os.path.join(temp_clone, ".git")):
            raise Exception("Prepared clone does not contain a .git folder.")
        _write_text_file(ready_file, "ready")
        _write_text_file(status_file, "Prepared clone is ready.")
        return temp_clone
    except Exception as clone_err:
        _write_text_file(fail_file, str(clone_err))
        _write_text_file(status_file, "Force Updater failed while preparing the clone.")
        raise


def _write_force_git_update_bat(prepared_clone, ready_file, fail_file, status_file):
    """Write a .bat that deletes the installed extension and moves in the prepared git clone."""
    out_dir = _force_pending_dir()
    bat_path = os.path.normpath(os.path.join(out_dir, "ForceUpdateWWPTools.bat"))
    ext_path = _force_extension_path()
    ext_parent = os.path.dirname(ext_path)
    temp_clone = os.path.normpath(prepared_clone)

    lines = [
        "@echo off",
        "setlocal",
        "title Force WWPTools Update",
        "echo.",
        "echo  Force WWPTools Update",
        "echo  =====================",
        "echo.",
        "echo  Preparing a fresh WWPTools git clone first.",
        "echo  Keep Revit open until this window says the clone is ready.",
        "echo.",
        "echo  Target extension folder:",
        'echo  "{ext}"'.format(ext=ext_path),
        "echo.",
        ":waitclone",
        "if exist \"{fail}\" (".format(fail=fail_file),
        "  echo.",
        "  echo  Force Updater failed while preparing the clone:",
        "  type \"{fail}\"".format(fail=fail_file),
        "  goto :fail",
        ")",
        "if not exist \"{ready}\" (".format(ready=ready_file),
        "  echo  Clone is not ready yet.",
        "  if exist \"{status}\" type \"{status}\"".format(status=status_file),
        "  echo.",
        "  echo  Waiting for pyRevit to finish downloading...",
        "  timeout /t 2 /nobreak >nul",
        "  cls",
        "  echo.",
        "  echo  Force WWPTools Update",
        "  echo  =====================",
        "  echo.",
        "  echo  Preparing a fresh WWPTools git clone first.",
        "  echo  Keep Revit open until this window says the clone is ready.",
        "  echo.",
        "  goto :waitclone",
        ")",
        "echo  Prepared clone is ready:",
        'echo  "{tmp}"'.format(tmp=temp_clone),
        "echo.",
        "echo  Now close all Revit windows, then press any key to continue.",
        "pause >nul",
        "echo.",
        ":waitrevit",
        'tasklist /FI "IMAGENAME eq Revit.exe" 2>nul | find /I "Revit.exe" >nul 2>&1',
        "if not errorlevel 1 (",
        "  echo  Revit is still running.",
        "  echo.",
        "  echo  Close all Revit windows, then press any key to check again.",
        "  pause >nul",
        "  cls",
        "  echo.",
        "  echo  Force WWPTools Update",
        "  echo  =====================",
        "  echo.",
        "  goto :waitrevit",
        ")",
        "echo  Revit has closed. Starting force update...",
        "echo.",
        "if not exist \"{tmp}\\.git\" (".format(tmp=temp_clone),
        "  echo  ERROR: Prepared git clone was not found.",
        "  echo.",
        "  echo  Run Force Updater again from Revit to regenerate it.",
        "  goto :fail",
        ")",
        "if not exist \"{parent}\" mkdir \"{parent}\"".format(parent=ext_parent),
        "echo  Removing old WWPTools extension...",
        "if exist \"{ext}\" if not exist \"{ext}\\*\" del /f /q \"{ext}\"".format(ext=ext_path),
        "if exist \"{ext}\" rd /s /q \"{ext}\"".format(ext=ext_path),
        "if exist \"{ext}\" goto :fail".format(ext=ext_path),
        "echo  Installing fresh git clone...",
        'move "{tmp}" "{ext}" >nul'.format(tmp=temp_clone, ext=ext_path),
        "if errorlevel 1 goto :fail",
        "if not exist \"{ext}\\.git\" goto :fail".format(ext=ext_path),
        "echo  Cleaning up temporary files...",
        "if exist \"{tmp}\" rd /s /q \"{tmp}\"".format(tmp=temp_clone),
        "if exist \"{ready}\" del /f /q \"{ready}\"".format(ready=ready_file),
        "if exist \"{fail}\" del /f /q \"{fail}\"".format(fail=fail_file),
        "if exist \"{status}\" del /f /q \"{status}\"".format(status=status_file),
        "goto :success",
        "",
        ":success",
        "echo.",
        "echo  WWPTools was force-updated successfully.",
        "echo  The extension now contains a .git folder for future updates.",
        "echo  Start Revit to use the new version.",
        "echo.",
        "goto :done",
        "",
        ":fail",
        "echo.",
        "echo  Force update FAILED. See the error above.",
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


def _launch_bat_in_console(bat_path):
    """Spawn the bat file in a new visible console window, detached from Revit.
    Returns True if the process launched successfully."""
    try:
        os.startfile(bat_path)
        return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=_CREATE_NEW_CONSOLE | _CREATE_NEW_PROCESS_GROUP,
        )
        return True
    except Exception:
        return False


def _launch_manual_deferred_update(repo_info, repo_root):
    target_branch = DEFAULT_UPDATE_BRANCH
    if repo_info is not None:
        current_branch = str(getattr(repo_info, "branch", "") or "").strip()
        if current_branch:
            target_branch = current_branch

    if repo_info is None:
        _prepare_full_zip_update(repo_root, target_branch)
        return

    bat_path = _write_deferred_update_bat(repo_root, target_branch)
    if not bat_path:
        _alert(
            "Could not create the external updater batch file.\n\n"
            "Please close Revit completely, then run Update WWPTools again.",
            TITLE,
        )
        return

    launched = _launch_bat_in_console(bat_path)
    if launched:
        _alert(
            "The external WWPTools updater was created and launched.\n\n"
            "Close all Revit windows, then press any key in the console.\n"
            "If Revit is still running, it will ask again.\n\n"
            "Script location:\n"
            "{}".format(bat_path),
            TITLE,
        )
    else:
        _alert(
            "The external WWPTools updater was created, but Windows did not launch it automatically.\n\n"
            "Close Revit, then double-click this script:\n"
            "{}".format(bat_path),
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


def _launch_force_git_update():
    pending_dir = _force_pending_dir()
    prepared_clone = _force_temp_clone_path()
    ready_file = os.path.normpath(os.path.join(pending_dir, "ForceClone.ready"))
    fail_file = os.path.normpath(os.path.join(pending_dir, "ForceClone.failed.txt"))
    status_file = os.path.normpath(os.path.join(pending_dir, "ForceClone.status.txt"))

    _delete_file_if_exists(ready_file)
    _delete_file_if_exists(fail_file)
    _write_text_file(status_file, "Starting Force Updater...")

    bat_path = _write_force_git_update_bat(prepared_clone, ready_file, fail_file, status_file)
    if not bat_path:
        _alert(
            "Could not create the Force Updater batch file.",
            TITLE,
        )
        return

    launched = _launch_bat_in_console(bat_path)
    if not launched:
        _alert(
            "Force Updater was created, but Windows did not launch it automatically.\n\n"
            "Double-click this script to watch the preparation and update process:\n"
            "{}".format(bat_path),
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

    try:
        _prepare_force_git_clone(prepared_clone, ready_file, fail_file, status_file)
    except Exception as clone_err:
        _alert(
            "Could not prepare a fresh git clone for Force Updater.\n\n"
            "{}\n\n"
            "Check the internet connection, then run Force Updater again.".format(clone_err),
            TITLE,
        )
        return

    if launched:
        _alert(
            "Force Updater finished preparing the fresh clone.\n\n"
            "The console window will now ask users to close all Revit windows,\n"
            "then press any key.\n"
            "If Revit is still running, it will ask again.\n\n"
            "After Revit closes, it will delete and recreate:\n"
            "{}\n\n"
            "The replacement clone is already prepared here:\n"
            "{}".format(
                _force_extension_path(),
                prepared_clone,
            ),
            TITLE,
        )


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
    if not _git_cli_available():
        if _confirm(
            "Git for Windows is not available on this machine.\n\n"
            "WWPTools can still update by downloading the '{}' branch from GitHub as a ZIP.\n"
            "The update will run after Revit closes and will replace the local extension folder.\n\n"
            "Prepare that update now?".format(target_branch),
            TITLE,
        ):
            _prepare_full_zip_update(repo_root, target_branch)
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
    update_changes = _classify_incoming_changes(repo_root, target_branch)
    has_dll_changes = update_changes["has_dll"]
    has_structural_changes = update_changes["has_structural"]

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
    if has_dll_changes:
        confirm_msg = (
            confirm_msg +
            "\n\nThis update includes DLL files that require Revit to be closed.\n"
            "A console updater will open. Close all Revit windows, then press any key\n"
            "in that console. If Revit is still running, it will ask again."
        )
    elif has_structural_changes:
        confirm_msg = (
            confirm_msg +
            "\n\nThis update changes folder structure, file names, or created/deleted files.\n"
            "WWPTools will update now. Please restart Revit afterwards to apply cleanly."
        )
    else:
        confirm_msg = (
            confirm_msg +
            "\n\nThis update only changes existing non-DLL files.\n"
            "WWPTools will replace those files without reloading pyRevit."
        )
    if not _confirm(confirm_msg, TITLE):
        return

    if has_dll_changes:
        bat_path = _write_deferred_update_bat(repo_root, target_branch)
        if bat_path:
            launched = _launch_bat_in_console(bat_path)
            if launched:
                _alert(
                    "A console window is waiting to finish the WWPTools update.\n\n"
                    "Close all Revit windows, then press any key in that console.\n"
                    "If Revit is still running, it will ask again.\n\n"
                    "Script location (if the window was blocked):\n"
                    "{}".format(bat_path),
                    TITLE,
                )
            else:
                _alert(
                    "DLL files require Revit to be closed before they can be replaced.\n\n"
                    "Double-click this script after closing Revit:\n"
                    "{}\n\n"
                    "It will check whether Revit is closed before updating.".format(bat_path),
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
                "DLL files require Revit to be closed before they can be replaced.\n\n"
                "Please close Revit completely, then run Update WWPTools again.",
                TITLE,
            )
        return

    try:
        updated_repo = _sync_to_github(repo_root, target_branch)
    except Exception as sync_err:
        if _is_revit_locked_update_error(sync_err):
            bat_path = _write_deferred_update_bat(repo_root, target_branch)
            if bat_path:
                launched = _launch_bat_in_console(bat_path)
                if launched:
                    _alert(
                        "A WWPTools DLL is locked by Revit.\n\n"
                        "A console window is waiting to finish the update.\n"
                        "Close all Revit windows, then press any key in that console.\n\n"
                        "Script location (if the window was blocked):\n"
                        "{}".format(bat_path),
                        TITLE,
                    )
                else:
                    _alert(
                        "A WWPTools DLL is locked by Revit.\n\n"
                        "Double-click this script after closing Revit:\n"
                        "{}".format(bat_path),
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
                    "A WWPTools DLL is locked by Revit.\n\n"
                    "Please close Revit completely, then run Update WWPTools again.",
                    TITLE,
                )
            return
        raise

    after_hash = updated_repo.last_commit_hash[:7]
    new_tag = _latest_tag(repo_root)
    after_label = "{} ({})".format(new_tag, after_hash) if new_tag else after_hash

    if has_structural_changes:
        _alert(
            "WWPTools updated successfully.\n\n"
            "Previous version: {}\n"
            "New version:      {}\n\n"
            "This update changed folder structure or file names.\n"
            "Please restart Revit to apply the changes cleanly.\n\n"
            "(A pyRevit hot-reload is not used here because it can cause\n"
            "'ribbon name already exists' errors when updating from older versions.)".format(
                current_label, after_label),
            TITLE,
        )
    else:
        _alert(
            "WWPTools updated successfully.\n\n"
            "Previous version: {}\n"
            "New version:      {}\n\n"
            "Only existing non-DLL files changed, so pyRevit was not reloaded.".format(
                current_label,
                after_label,
            ),
            TITLE,
        )


def main():
    repo_root = _extension_root()
    repo_info = _discover_repo(repo_root)
    if FORCE_GENERATE_UPDATER:
        _launch_force_git_update()
        return
    if MANUAL_GENERATE_UPDATER:
        _launch_manual_deferred_update(repo_info, repo_root)
        return
    if not repo_info:
        _show_not_repo_message()
        return
    _update_repo(repo_info, repo_root)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _alert(traceback.format_exc(), TITLE)
