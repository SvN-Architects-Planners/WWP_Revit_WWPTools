import os
import clr
clr.AddReference('RevitAPI')
import subprocess
import ctypes
from WWP_compat import configparser, io_open, read_config_file

app = __revit__.Application

confirm_response = ctypes.windll.user32.MessageBoxW(
    None,
    "Make sure you close all ACC/BIM360 files, and run only with an empty Revit file open, if not sure, please contact Jason Tian",
    "Confirmation",
    1 | 0x30  # 1: OK, 0x30: Information icon
)

if confirm_response == 1:
    revit_version = str(app.VersionNumber)
    revit_cloud_local_path = ""
    path = os.path.join(os.environ.get('LOCALAPPDATA'), "Autodesk", "Revit", "Autodesk Revit " + revit_version)
    temp_path = os.path.join(os.environ.get('LOCALAPPDATA'), 'TEMP')
    collab_cache_path = os.path.join(path, 'CollaborationCache')
    journal_path = os.path.join(path, 'Journals')
    revit_ini_path = os.path.join(os.environ.get('APPDATA'), "Autodesk", "Revit", "Autodesk Revit " + revit_version, "Revit.ini")
    if os.path.isfile(revit_ini_path):
        config = configparser.ConfigParser()
        config.optionxform = str
        try:
            with io_open(revit_ini_path, 'r', encoding='utf-16') as ini_file:
                read_config_file(config, ini_file)
        except Exception:
            try:
                with io_open(revit_ini_path, 'r', encoding='utf-8-sig', errors='ignore') as ini_file:
                    read_config_file(config, ini_file)
            except Exception:
                with io_open(revit_ini_path, 'r', encoding='cp1252', errors='ignore') as ini_file:
                    read_config_file(config, ini_file)
        if config.has_option("CloudModelCache", "CacheLocation"):
            revit_cloud_local_path = config.get("CloudModelCache", "CacheLocation")

    subprocess.Popen(['explorer', collab_cache_path])
    subprocess.Popen(['explorer', journal_path])
    subprocess.Popen(['explorer', temp_path])
    subprocess.Popen(['explorer', revit_cloud_local_path])

    stats = {
        "deleted_files": 0,
        "deleted_folders": 0,
        "skipped_targets": 0,
        "failed_files": 0,
        "failed_folders": 0,
    }

    def delete_files_and_folders(directory):
        if not directory or not os.path.isdir(directory):
            stats["skipped_targets"] += 1
            return
        for root, dirs, files in os.walk(directory, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    os.remove(file_path)
                    stats["deleted_files"] += 1
                except Exception:
                    stats["failed_files"] += 1
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    os.rmdir(dir_path)
                    stats["deleted_folders"] += 1
                except Exception:
                    stats["failed_folders"] += 1

    delete_files_and_folders(collab_cache_path)
    delete_files_and_folders(journal_path)
    delete_files_and_folders(temp_path)
    delete_files_and_folders(revit_cloud_local_path)

    print(
        "Cleaning complete.\n"
        "Deleted: {0} ({1} files, {2} folders).\n"
        "Skipped: {3} missing target directories.\n"
        "Failed: {4} ({5} files, {6} folders).".format(
            stats["deleted_files"] + stats["deleted_folders"],
            stats["deleted_files"],
            stats["deleted_folders"],
            stats["skipped_targets"],
            stats["failed_files"] + stats["failed_folders"],
            stats["failed_files"],
            stats["failed_folders"],
        )
    )

else:
    print("Cleaning process cancelled.")
