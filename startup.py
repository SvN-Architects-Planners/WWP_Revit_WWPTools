"""
Runs at pyRevit startup. Checks for WWPTools updates in the background, then
offers to install automatically after the user closes Revit.
"""
import os
import subprocess
import sys
import tempfile
import threading

_TARGET_BRANCH = "main"

_DETACHED_PROCESS       = getattr(subprocess, "DETACHED_PROCESS",       0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# Results written by background thread, read by close handler
_update_available = False
_remote_tag = None
_check_done = threading.Event()


def _extension_root():
    return os.path.dirname(os.path.abspath(__file__))


def _git_cli_available():
    try:
        p = subprocess.Popen(["git", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False)
        p.communicate()
        return p.returncode == 0
    except Exception:
        return False


def _git_output(repo_root, args):
    p = subprocess.Popen(
        ["git", "-C", repo_root] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
    )
    stdout, _ = p.communicate()
    if p.returncode != 0:
        raise Exception("git failed")
    return (stdout or b"").decode("utf-8", "ignore")


def _background_check():
    global _update_available, _remote_tag
    try:
        if not _git_cli_available():
            return
        root = _extension_root()
        _git_output(root, ["fetch", "origin", _TARGET_BRANCH])
        behind = int(_git_output(root, ["rev-list", "--count", "HEAD..origin/{}".format(_TARGET_BRANCH)]).strip())
        _update_available = behind > 0
        if _update_available:
            try:
                tag = _git_output(root, ["describe", "--tags", "--abbrev=0", "origin/{}".format(_TARGET_BRANCH)]).strip()
                _remote_tag = tag if tag else None
            except Exception:
                pass
    except Exception:
        pass
    finally:
        _check_done.set()


def _powershell_toast(title, message):
    safe_title = (title or "").replace("'", "''")
    safe_msg   = (message or "").replace("'", "''")
    return (
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \""
        "try {{ "
        "Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction Stop; "
        "$xml = '<toast><visual><binding template=\\'ToastGeneric\\'>"
        "<text>{t}</text><text>{m}</text>"
        "</binding></visual></toast>'; "
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument; $doc.LoadXml($xml); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc); "
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier('WWP Architects + Planners'); $notifier.Show($toast); "
        "}} catch {{ "
        "try {{ Add-Type -AssemblyName PresentationFramework; "
        "[System.Windows.MessageBox]::Show('{m}', '{t}') }} catch {{}} "
        "}}\""
    ).format(t=safe_title, m=safe_msg)


def _schedule_update(repo_root):
    pid = os.getpid()
    safe_root = os.path.normpath(repo_root)

    base_dir = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    log_dir = os.path.normpath(os.path.join(base_dir, "WWPTools", "UpdateLogs"))
    try:
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
    except Exception:
        log_dir = tempfile.gettempdir()
    log_path = os.path.normpath(os.path.join(log_dir, "wwptools_autoupdate_{}.log".format(pid)))

    label = _remote_tag or "latest"
    start_toast   = _powershell_toast("Update WWPTools", "WWPTools update is running in the background...")
    success_toast = _powershell_toast("Update WWPTools", "WWPTools updated to {}. Restart Revit to apply.".format(label))
    failed_toast  = _powershell_toast("Update WWPTools", "WWPTools update failed. Check log:\n{}".format(log_path))

    batch = "\r\n".join([
        "@echo off",
        "setlocal",
        "set \"LOG={log}\"".format(log=log_path),
        "echo [%%date%% %%time%%] Auto-updater started. > \"%%LOG%%\"",
        "echo [%%date%% %%time%%] Waiting for Revit.exe to close... >> \"%%LOG%%\"",
        ":wait",
        "tasklist /FI \"IMAGENAME eq Revit.exe\" 2>NUL | find /I \"Revit.exe\" >NUL",
        "if not errorlevel 1 (timeout /t 3 /nobreak >NUL & goto wait)",
        "echo [%%date%% %%time%%] Revit closed. Waiting 15s for cleanup... >> \"%%LOG%%\"",
        "timeout /t 15 /nobreak >NUL",
        start_toast,
        "echo [%%date%% %%time%%] Starting Git sync... >> \"%%LOG%%\"",
        "git -C \"{root}\" fetch origin {branch} >> \"%%LOG%%\" 2>&1".format(root=safe_root, branch=_TARGET_BRANCH),
        "if errorlevel 1 goto failed",
        "git -C \"{root}\" reset --hard origin/{branch} >> \"%%LOG%%\" 2>&1".format(root=safe_root, branch=_TARGET_BRANCH),
        "if errorlevel 1 goto failed",
        "git -C \"{root}\" clean -ffdx >> \"%%LOG%%\" 2>&1".format(root=safe_root),
        "if errorlevel 1 goto failed",
        "echo [%%date%% %%time%%] Update completed. >> \"%%LOG%%\"",
        success_toast,
        "exit /b 0",
        ":failed",
        "echo [%%date%% %%time%%] Update failed. >> \"%%LOG%%\"",
        failed_toast,
        "exit /b 1",
    ]) + "\r\n"

    batch_path = os.path.join(tempfile.gettempdir(), "wwptools_autoupdate_{}.bat".format(pid))
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


def _on_application_closing(sender, args):
    # Wait up to 5s for the background check to finish (it usually completes well before close)
    _check_done.wait(5)
    if not _update_available:
        return
    try:
        import clr  # type: ignore
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons, TaskDialogResult  # type: ignore

        version_text = " to {}".format(_remote_tag) if _remote_tag else ""
        dialog = TaskDialog("Update WWPTools")
        dialog.MainInstruction = "WWPTools Update Available"
        dialog.MainContent = (
            "A WWPTools update is available{}.\n\n"
            "Install automatically after Revit closes?\n\n"
            "(You will get a notification when the update finishes.)"
        ).format(version_text)
        dialog.CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No
        dialog.DefaultButton = TaskDialogResult.Yes

        if dialog.Show() == TaskDialogResult.Yes:
            _schedule_update(_extension_root())
    except Exception:
        pass


def _register():
    try:
        __revit__.ApplicationClosing += _on_application_closing  # type: ignore
    except Exception:
        pass


# Kick off background update check and register the close handler
threading.Thread(target=_background_check, name="WWPTools-UpdateCheck").start()
_register()