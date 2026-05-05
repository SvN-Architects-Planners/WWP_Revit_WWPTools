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

    print(collab_cache_path)
    print(journal_path)
    print(temp_path)
    print(revit_cloud_local_path)
    
    subprocess.Popen(['explorer', collab_cache_path])
    subprocess.Popen(['explorer', journal_path])
    subprocess.Popen(['explorer', temp_path])
    subprocess.Popen(['explorer', revit_cloud_local_path])

    def delete_files_and_folders(directory):
        if not directory or not os.path.isdir(directory):
            print("Skip missing directory: " + str(directory))
            return
        for root, dirs, files in os.walk(directory, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    os.remove(file_path)
                    print("Deleted file: " + file_path)
                except Exception as e:
                    print("Error deleting file: " + file_path)
                    print("Error message: " + str(e))
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    print("Error deleting folder: " + dir_path)
                    print("Error message: " + str(e))

    delete_files_and_folders(collab_cache_path)
    delete_files_and_folders(journal_path)
    delete_files_and_folders(temp_path)
    delete_files_and_folders(revit_cloud_local_path)

else:
    print("Cleaning process cancelled.")
