# import libraries
import clr
import os
import ctypes
import shutil
from System import String, Int32
from System.Collections.Generic import List

_DLL_LOADED = False
_DIALOGS = None
_LAST_ERROR = None
_LAST_PATH = None
_THEME_LOADED = False


def _revit_version_number():
	try:
		return int(str(__revit__.Application.VersionNumber))
	except Exception:
		return None


def _dll_path():
	cur_dir = os.path.dirname(os.path.abspath(__file__))
	version = _revit_version_number()
	if version and version >= 2025:
		return os.path.join(cur_dir, "WWPTools.WpfUI.net8.0-windows.dll")
	return os.path.join(cur_dir, "WWPTools.WpfUI.net48.dll")


def _is_remote_path(path):
	if not path:
		return False
	if path.startswith("\\\\"):
		return True
	drive, _ = os.path.splitdrive(path)
	if not drive:
		return False
	try:
		drive_root = drive + "\\"
		drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_root)
		return drive_type == 4  # DRIVE_REMOTE
	except Exception:
		return False


def _copy_local(dll_path):
	temp_root = os.environ.get("TEMP") or os.environ.get("TMP") or os.getcwd()
	temp_dir = os.path.join(temp_root, "WWPTools.WpfUI")
	try:
		if not os.path.isdir(temp_dir):
			os.makedirs(temp_dir)
	except Exception:
		return dll_path

	local_dll = os.path.join(temp_dir, os.path.basename(dll_path))
	try:
		if not os.path.isfile(local_dll) or os.path.getmtime(local_dll) < os.path.getmtime(dll_path):
			shutil.copy2(dll_path, local_dll)
	except Exception:
		return dll_path

	logo_src = os.path.join(os.path.dirname(dll_path), "WWPtools-logo.png")
	logo_dst = os.path.join(temp_dir, "WWPtools-logo.png")
	try:
		if os.path.isfile(logo_src):
			if not os.path.isfile(logo_dst) or os.path.getmtime(logo_dst) < os.path.getmtime(logo_src):
				shutil.copy2(logo_src, logo_dst)
	except Exception:
		pass

	return local_dll


def _load_wpf():
	global _DLL_LOADED
	global _DIALOGS
	global _LAST_ERROR
	global _LAST_PATH
	if _DLL_LOADED:
		return _DIALOGS is not None
	_DLL_LOADED = True
	dll_path = _dll_path()
	if not os.path.isfile(dll_path):
		_LAST_ERROR = "Missing WPF DLL at " + dll_path
		_LAST_PATH = dll_path
		return False
	try:
		load_path = _copy_local(dll_path) if _is_remote_path(dll_path) else dll_path
		_LAST_PATH = load_path
		if hasattr(clr, "AddReferenceToFileAndPath"):
			clr.AddReferenceToFileAndPath(load_path)
		else:
			clr.AddReference(load_path)
		from WWPTools.WpfUI import DialogService
		_DIALOGS = DialogService
		return True
	except Exception as ex:
		_LAST_ERROR = str(ex)
		_DIALOGS = None
		return False


def _ensure_wpf():
	if not _load_wpf():
		message = "WPF UI library not available. Build WWPTools.WpfUI and ensure the DLL is in WWPTools.extension\\lib."
		if _LAST_PATH:
			message += "\nTried to load: " + _LAST_PATH
		if _LAST_ERROR:
			message += "\nLoad error: " + _LAST_ERROR
		raise Exception(message)

def _to_net_string_list(items):
	if items is None:
		return None
	net_list = List[String]()
	for item in items:
		net_list.Add("" if item is None else str(item))
	return net_list


def _to_net_string_list_list(items):
	if items is None:
		return None
	net_outer = List[List[String]]()
	for inner in items:
		net_outer.Add(_to_net_string_list(inner) or List[String]())
	return net_outer


def _to_net_int_list(items):
	if items is None:
		return None
	net_list = List[Int32]()
	for item in items:
		try:
			net_list.Add(int(item))
		except Exception:
			continue
	return net_list


# ---------------------------------------------------------------------------
# Helpers for XAML-file-based dialogs (no compiled DLL required)
# ---------------------------------------------------------------------------

def _get_lib_dir():
	return os.path.dirname(os.path.abspath(__file__))


def _ensure_application():
	clr.AddReference("PresentationFramework")
	clr.AddReference("PresentationCore")
	clr.AddReference("WindowsBase")
	from System.Windows import Application, ShutdownMode
	if Application.Current is None:
		app = Application()
		app.ShutdownMode = ShutdownMode.OnExplicitShutdown


def _ensure_theme():
	global _THEME_LOADED
	if _THEME_LOADED:
		return
	_ensure_application()
	from System.Windows import Application
	from System.Windows.Markup import XamlReader
	try:
		if Application.Current.Resources.Contains("PanelBrush"):
			_THEME_LOADED = True
			return
	except Exception:
		pass
	theme_path = os.path.join(_get_lib_dir(), "FlatTheme.xaml")
	if not os.path.isfile(theme_path):
		return
	with open(theme_path, "r", encoding="utf-8") as f:
		content = f.read()
	theme_dict = XamlReader.Parse(content)
	Application.Current.Resources.MergedDictionaries.Add(theme_dict)
	_THEME_LOADED = True


def _load_window_xaml(xaml_filename):
	from System.Windows.Markup import XamlReader
	xaml_path = os.path.join(_get_lib_dir(), xaml_filename)
	with open(xaml_path, "r", encoding="utf-8") as f:
		content = f.read()
	return XamlReader.Parse(content)


def _load_window_xaml_path(xaml_path):
	from System.Windows.Markup import XamlReader
	with open(xaml_path, "r", encoding="utf-8") as f:
		content = f.read()
	return XamlReader.Parse(content)


def _get_owner_handle():
	try:
		from System.Diagnostics import Process
		return Process.GetCurrentProcess().MainWindowHandle
	except Exception:
		from System import IntPtr
		return IntPtr.Zero


def _find_first_visual_child(parent, target_type):
	from System.Windows.Media import VisualTreeHelper
	count = VisualTreeHelper.GetChildrenCount(parent)
	for i in range(count):
		child = VisualTreeHelper.GetChild(parent, i)
		if isinstance(child, target_type):
			return child
		found = _find_first_visual_child(child, target_type)
		if found is not None:
			return found
	return None


def uiUtils_alert(message, title="Message"):
	text = "" if message is None else str(message)
	caption = "" if title is None else str(title)
	try:
		_ensure_wpf()
		if _DIALOGS is not None and hasattr(_DIALOGS, "Alert"):
			_DIALOGS.Alert(text, caption)
			return
	except Exception:
		pass

	try:
		clr.AddReference("RevitAPIUI")
		from Autodesk.Revit import UI
		UI.TaskDialog.Show(caption or "Message", text)
	except Exception:
		raise Exception(text)


def uiUtils_confirm(message, title="Confirm"):
	_ensure_wpf()
	return bool(_DIALOGS.Confirm(message, title))


def uiUtils_select_indices(items, title="Select Items", prompt="Select items:", multiselect=True, width=980, height=540):
	_ensure_wpf()
	items_list = _to_net_string_list(items) or List[String]()
	selected = _DIALOGS.SelectIndices(items_list, title, prompt, multiselect, int(width), int(height))
	return list(selected) if selected is not None else []


def uiUtils_select_items_with_mode(
	items,
	title="Select Items",
	prompt="Select items:",
	mode_labels=("Option A", "Option B"),
	default_mode=0,
	prechecked_indices=None,
	width=720,
	height=620,
):
	_ensure_wpf()
	items_list = _to_net_string_list(items) or List[String]()
	labels = list(mode_labels) if mode_labels is not None else []
	label_a = labels[0] if len(labels) > 0 else "Option A"
	label_b = labels[1] if len(labels) > 1 else "Option B"
	prechecked = _to_net_int_list(prechecked_indices) or List[Int32]()
	result = _DIALOGS.SelectItemsWithMode(items_list, title, prompt, label_a, label_b, int(default_mode), prechecked, int(width), int(height))
	if result is None:
		return [], None
	return list(result.SelectedIndices), int(result.Mode)


def uiUtils_export_schedules_inputs(
	items,
	title="Export Schedules",
	prompt="Select schedules to export:",
	mode_labels=("Export to Excel", "Export to CSV"),
	default_mode=0,
	prechecked_indices=None,
	excel_path="",
	csv_folder="",
	csv_delimiter=",",
	csv_quote_all=False,
	width=860,
	height=720,
):
	_ensure_wpf()
	if not hasattr(_DIALOGS, "ExportSchedulesInputs"):
		return False
	items_list = _to_net_string_list(items) or List[String]()
	labels = list(mode_labels) if mode_labels is not None else []
	label_a = labels[0] if len(labels) > 0 else "Export to Excel"
	label_b = labels[1] if len(labels) > 1 else "Export to CSV"
	prechecked = _to_net_int_list(prechecked_indices) or List[Int32]()
	result = _DIALOGS.ExportSchedulesInputs(
		items_list,
		title,
		prompt,
		label_a,
		label_b,
		int(default_mode),
		prechecked,
		excel_path or "",
		csv_folder or "",
		csv_delimiter or ",",
		bool(csv_quote_all),
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"selected_indices": list(result.SelectedIndices),
		"mode": int(result.Mode),
		"excel_path": result.ExcelPath or "",
		"csv_folder": result.CsvFolder or "",
		"csv_delimiter": result.CsvDelimiter or ",",
		"csv_quote_all": bool(result.CsvQuoteAll),
	}


def uiUtils_select_items_with_filter(
	items,
	filter_param_names,
	filter_values_by_param,
	title="Select Items",
	prompt="Select items:",
	prechecked_indices=None,
	default_filter_param="",
	default_filter_value="",
	width=980,
	height=720,
):
	_ensure_wpf()
	items_list = _to_net_string_list(items) or List[String]()
	param_list = _to_net_string_list(filter_param_names) or List[String]()
	values_matrix = _to_net_string_list_list(filter_values_by_param) or List[List[String]]()
	prechecked = _to_net_int_list(prechecked_indices) or List[Int32]()
	result = _DIALOGS.SelectItemsWithFilter(
		items_list,
		title,
		prompt,
		param_list,
		values_matrix,
		prechecked,
		default_filter_param or "",
		default_filter_value or "",
		int(width),
		int(height),
	)
	if result is None:
		return [], "", ""
	return list(result.SelectedIndices), str(result.FilterParameter or ""), str(result.FilterValue or "")


def uiUtils_project_upgrader_options(
	title="Project Upgrader",
	description="Select folder containing Revit files",
	include_subfolders_label="Include subfolders",
	cancel_text="Cancel",
	width=520,
	height=260,
	initial_folder="",
):
	_ensure_wpf()
	result = _DIALOGS.ProjectUpgraderOptions(
		title,
		description,
		include_subfolders_label,
		"OK",
		cancel_text or "Cancel",
		int(width),
		int(height),
		initial_folder or "",
	)
	if result is None:
		return None
	return {
		"folder": result.Folder or "",
		"include_subfolders": bool(result.IncludeSubfolders),
	}


def uiUtils_select_sheet_renumber_inputs(
	categories=None,
	print_sets=None,
	title="Renumber Sheets",
	category_label="Choose Sheet Category",
	printset_label="Choose Print Set",
	starting_label="Starting Number",
	cancel_text="Cancel",
	width=520,
	height=320,
):
	_ensure_wpf()
	categories_list = _to_net_string_list(categories)
	print_sets_list = _to_net_string_list(print_sets)
	result = _DIALOGS.SelectSheetRenumberInputs(
		categories_list,
		print_sets_list,
		title,
		category_label,
		printset_label,
		starting_label,
		"Set Values",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"category": result.Category or "",
		"printset": result.PrintSet or "",
		"starting_number": result.StartingNumber or "",
	}


def uiUtils_select_sheet_renumber_inputs_with_list(
	items,
	title="Renumber Sheets",
	prompt="Select sheets to renumber:",
	starting_label="Starting Number",
	cancel_text="Cancel",
	width=980,
	height=620,
):
	_ensure_wpf()
	items_list = _to_net_string_list(items) or List[String]()
	result = _DIALOGS.SelectSheetRenumberInputsWithList(
		items_list,
		title,
		prompt,
		starting_label,
		"Set Values",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"selected_indices": list(result.SelectedIndices),
		"starting_number": result.StartingNumber or "",
		"iso19650_mode": bool(getattr(result, "Iso19650Mode", False)),
	}


def uiUtils_viewname_replace_inputs(
	title="Replace View Name",
	find_label="Find",
	replace_label="Replace",
	prefix_label="Prefix",
	suffix_label="Suffix",
	cancel_text="Cancel",
	width=520,
	height=320,
):
	_ensure_wpf()
	result = _DIALOGS.ViewnameReplaceInputs(
		title,
		find_label,
		replace_label,
		prefix_label,
		suffix_label,
		"Apply",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"find": result.Find or "",
		"replace": result.Replace or "",
		"prefix": result.Prefix or "",
		"suffix": result.Suffix or "",
	}


def uiUtils_duplicate_sheet_inputs(
	items,
	title="Sheet Duplicator",
	prompt="Select sheets to duplicate:",
	options_label="Duplicate Options",
	duplicate_with_views_label="Duplicate with Views",
	prefix_label="Sheet Number Prefix",
	suffix_label="Sheet Number Suffix",
	cancel_text="Cancel",
	width=980,
	height=700,
):
	_ensure_wpf()
	items_list = _to_net_string_list(items) or List[String]()
	result = _DIALOGS.DuplicateSheetInputs(
		items_list,
		title,
		prompt,
		options_label,
		duplicate_with_views_label,
		prefix_label,
		suffix_label,
		"Duplicate",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"selected_indices": list(result.SelectedIndices),
		"duplicate_with_views": bool(result.DuplicateWithViews),
		"duplicate_option": int(result.DuplicateOption),
		"prefix": result.Prefix or "",
		"suffix": result.Suffix or "",
	}


def uiUtils_open_file_dialog(title="Open File", filter_text="All files (*.*)|*.*", multiselect=False, initial_directory=""):
	_ensure_wpf()
	result = _DIALOGS.OpenFileDialog(
		title,
		filter_text,
		bool(multiselect),
		initial_directory or "",
	)
	if not result:
		return [] if multiselect else None
	if multiselect:
		return [path for path in str(result).split("|") if path]
	return result


def uiUtils_save_file_dialog(title="Save File", filter_text="All files (*.*)|*.*", default_extension="", initial_directory="", file_name=""):
	_ensure_wpf()
	return _DIALOGS.SaveFileDialog(
		title,
		filter_text,
		default_extension or "",
		initial_directory or "",
		file_name or "",
	)


def uiUtils_select_folder_dialog(title="Select Folder", initial_directory=""):
	_ensure_wpf()
	return _DIALOGS.SelectFolderDialog(
		title,
		initial_directory or "",
	)


def uiUtils_prompt_text(title="Input", prompt="Enter value:", default_value="", ok_text="OK", cancel_text="Cancel", width=420, height=220):
	_ensure_wpf()
	return _DIALOGS.PromptText(
		title,
		prompt,
		default_value or "",
		ok_text or "OK",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)


def uiUtils_show_text_report(title, text, ok_text="OK", cancel_text=None, width=700, height=520):
	_ensure_wpf()
	return bool(_DIALOGS.ShowTextReport(
		title,
		text,
		ok_text or "OK",
		cancel_text,
		int(width),
		int(height),
	))


def uiUtils_find_replace(title="Find and Replace", find_label="Find", replace_label="Replace", ok_text="OK", cancel_text="Cancel", width=420, height=200):
	_ensure_wpf()
	result = _DIALOGS.FindReplaceDialog(
		title,
		find_label,
		replace_label,
		ok_text or "OK",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"find": result.FindText or "",
		"replace": result.ReplaceText or "",
	}


def uiUtils_random_tree_settings(title="Random Tree", rotation_label="Random Rotation", size_label="Random Size", percent_label="Size variance (%)", default_percent=30, width=320, height=220):
	_ensure_wpf()
	result = _DIALOGS.RandomTreeSettings(
		title,
		rotation_label,
		size_label,
		percent_label,
		float(default_percent),
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"random_rotation": bool(result.RandomRotation),
		"random_size": bool(result.RandomSize),
		"percent": float(result.Percent),
	}


def uiUtils_parameter_copy_inputs(param_names, title="Copy and Transform Parameter", source_default="", target_default="", find_default="", replace_default="", prefix_default="", suffix_default="", width=460, height=420):
	_ensure_wpf()
	names = _to_net_string_list(param_names) or List[String]()
	result = _DIALOGS.ParameterCopyInputs(
		names,
		title,
		source_default or "",
		target_default or "",
		find_default or "",
		replace_default or "",
		prefix_default or "",
		suffix_default or "",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"source_param": result.SourceParam or "",
		"target_param": result.TargetParam or "",
		"find_text": result.FindText or "",
		"replace_text": result.ReplaceText or "",
		"prefix": result.Prefix or "",
		"suffix": result.Suffix or "",
	}


def uiUtils_duplicate_view_options(option_labels, option_values, default_index=0, title="Duplicate Views", description="", prefix_default="", suffix_default="", ok_text="Set Values", width=520, height=360):
	_ensure_wpf()
	labels = _to_net_string_list(option_labels) or List[String]()
	values = _to_net_string_list(option_values) or List[String]()
	result = _DIALOGS.DuplicateViewOptions(
		labels,
		values,
		int(default_index),
		title,
		description or "",
		prefix_default or "",
		suffix_default or "",
		ok_text or "Set Values",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"prefix": result.Prefix or "",
		"suffix": result.Suffix or "",
		"duplicate_option": result.OptionValue or "",
	}


def uiUtils_marketing_view_options(
	sheet_params,
	template_names,
	titleblock_names,
	keyplan_template_names,
	fill_type_names,
	title="Make Marketing View",
	area_label="(not selected)",
	door_label="",
	keyplan_enabled=False,
	overwrite_existing=False,
	template_index=0,
	titleblock_index=0,
	keyplan_template_index=0,
	fill_type_index=0,
	sheet_number_param="",
	sheet_name_param="",
	width=720,
	height=660,
):
	_ensure_wpf()
	result = _DIALOGS.MarketingViewOptions(
		_to_net_string_list(sheet_params) or List[String](),
		_to_net_string_list(template_names) or List[String](),
		_to_net_string_list(titleblock_names) or List[String](),
		_to_net_string_list(keyplan_template_names) or List[String](),
		_to_net_string_list(fill_type_names) or List[String](),
		title,
		area_label,
		door_label,
		bool(keyplan_enabled),
		bool(overwrite_existing),
		int(template_index),
		int(titleblock_index),
		int(keyplan_template_index),
		int(fill_type_index),
		sheet_number_param or "",
		sheet_name_param or "",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"sheet_number_param": result.SheetNumberParam or "",
		"sheet_name_param": result.SheetNameParam or "",
		"template_index": int(result.TemplateIndex),
		"titleblock_index": int(result.TitleblockIndex),
		"keyplan_enabled": bool(result.KeyplanEnabled),
		"keyplan_template_index": int(result.KeyplanTemplateIndex),
		"fill_type_index": int(result.FillTypeIndex),
		"overwrite_existing": bool(result.OverwriteExisting),
	}


def uiUtils_keyplan_options(
	template_names,
	fill_type_names,
	title="Make Keyplans",
	area_label="(not selected)",
	template_index=0,
	fill_type_index=0,
	width=640,
	height=360,
):
	_ensure_wpf()
	result = _DIALOGS.KeyplanOptions(
		_to_net_string_list(template_names) or List[String](),
		_to_net_string_list(fill_type_names) or List[String](),
		title,
		area_label,
		int(template_index),
		int(fill_type_index),
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"template_index": int(result.TemplateIndex),
		"fill_type_index": int(result.FillTypeIndex),
	}


def uiUtils_area_keyplan_import(
	title="Import Area Key Schedule",
	file_path="",
	column_names=None,
	target_types=None,
	selected_target_type="",
	parameter_options=None,
	default_selections=None,
	width=980,
	height=720,
):
	_ensure_application()
	_ensure_theme()

	from System.Windows.Interop import WindowInteropHelper
	from System.Windows.Controls import ComboBox as WpfComboBox
	from System.Collections import ArrayList
	from System.Collections.Generic import List as NetList

	extension_dir = os.path.dirname(_get_lib_dir())
	script_xaml_path = os.path.join(
		extension_dir,
		"WWPTools.tab",
		"3. Manage.panel",
		"Import Key Schedule.pulldown",
		"Import from Excel.pushbutton",
		"AreaKeyplanImportWindow.xaml",
	)
	if os.path.isfile(script_xaml_path):
		window = _load_window_xaml_path(script_xaml_path)
	else:
		window = _load_window_xaml("AreaKeyplanImportWindow.xaml")
	window.Title = title or "Import Area Key Schedule"
	if width > 0:
		window.Width = width
	if height > 0:
		window.Height = height

	owner = _get_owner_handle()
	try:
		from System import IntPtr
		if owner != IntPtr.Zero:
			WindowInteropHelper(window).Owner = owner
	except Exception:
		pass

	file_path_box   = window.FindName("FilePathBox")
	browse_button   = window.FindName("BrowseButton")
	load_button     = window.FindName("LoadButton")
	target_combo    = window.FindName("TargetTypeCombo")
	mapping_grid    = window.FindName("MappingGrid")
	ok_button       = window.FindName("OkButton")
	cancel_button   = window.FindName("CancelButton")
	logo_image      = window.FindName("LogoImage")

	if file_path_box is not None:
		file_path_box.Text = file_path or ""

	types_list = [str(t) for t in target_types] if target_types else []
	if target_combo is not None:
		for t in types_list:
			target_combo.Items.Add(t)
		if selected_target_type and selected_target_type in types_list:
			target_combo.SelectedItem = selected_target_type
		elif target_combo.Items.Count > 0:
			target_combo.SelectedIndex = 0

	options = [str(o) for o in parameter_options] if parameter_options else []
	columns = [str(c) for c in column_names] if column_names else []
	defaults = [str(d) for d in default_selections] if default_selections else []
	net_options = NetList[String]()
	for option in options:
		net_options.Add(option)

	class _ColumnMapping(object):
		def __init__(self, col, sel):
			self.ColumnName = col
			self.SelectedOption = sel

	mappings = []
	for i, col in enumerate(columns):
		sel = defaults[i] if i < len(defaults) else ""
		if not sel or sel not in options:
			sel = options[0] if options else ""
		mappings.append(_ColumnMapping(col, sel))

	if mapping_grid is not None:
		mapping_grid.Tag = net_options
		net_items = ArrayList()
		for m in mappings:
			net_items.Add(m)
		mapping_grid.ItemsSource = net_items
		has_data = len(mappings) > 0
		mapping_grid.IsEnabled = has_data
		if ok_button is not None:
			ok_button.IsEnabled = has_data

	if logo_image is not None:
		try:
			from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
			from System import Uri
			logo_path = os.path.join(_get_lib_dir(), "WWPtools-logo.png")
			if os.path.isfile(logo_path):
				bmp = BitmapImage()
				bmp.BeginInit()
				bmp.CacheOption = BitmapCacheOption.OnLoad
				bmp.UriSource = Uri(logo_path)
				bmp.EndInit()
				logo_image.Source = bmp
		except Exception:
			pass

	result_state = {"load_requested": False}

	def on_browse(sender, e):
		from Microsoft.Win32 import OpenFileDialog
		dlg = OpenFileDialog()
		dlg.Title = "Select Excel File"
		dlg.Filter = "Excel Files (*.xlsx)|*.xlsx|All Files (*.*)|*.*"
		dlg.CheckFileExists = True
		if dlg.ShowDialog() == True:
			file_path_box.Text = dlg.FileName or ""

	def on_ok(sender, e):
		result_state["load_requested"] = False
		window.DialogResult = True

	def on_load(sender, e):
		result_state["load_requested"] = True
		window.DialogResult = True

	def on_cancel(sender, e):
		window.DialogResult = False

	if browse_button is not None:
		browse_button.Click += on_browse
	if ok_button is not None:
		ok_button.Click += on_ok
	if load_button is not None:
		load_button.Click += on_load
	if cancel_button is not None:
		cancel_button.Click += on_cancel

	if window.ShowDialog() != True:
		return None

	result_file_path = str(file_path_box.Text or "") if file_path_box is not None else ""
	result_target_type = str(target_combo.SelectedItem or "") if target_combo is not None else ""

	col_names_out = []
	selected_options_out = []

	if mapping_grid is not None:
		mapping_grid.UpdateLayout()
		for i in range(mapping_grid.Items.Count):
			item = mapping_grid.Items[i]
			col_names_out.append(str(getattr(item, "ColumnName", "") or ""))
			row = mapping_grid.ItemContainerGenerator.ContainerFromIndex(i)
			if row is not None:
				combo = _find_first_visual_child(row, WpfComboBox)
				sel = str(combo.SelectedItem or "") if combo is not None else str(getattr(item, "SelectedOption", "") or "")
			else:
				sel = str(getattr(item, "SelectedOption", "") or "")
			selected_options_out.append(sel)

	return {
		"load_requested": result_state["load_requested"],
		"selected_target_type": result_target_type,
		"file_path": result_file_path,
		"column_names": col_names_out,
		"selected_options": selected_options_out,
	}


def uiUtils_level_setup_inputs(
	title="Level Setup",
	level_count_label="How many levels are needed?",
	height12_label="Level 1 to Level 2 height (mm):",
	height23_label="Level 2 to Level 3 height (mm):",
	typical_height_label="Typical floor-to-floor height after Level 3 (mm):",
	underground_count_label="How many underground levels?",
	height_p1_to_l1_label="Floor-to-floor height from P1 to Level 1 (mm):",
	typical_depth_label="Typical depth below (P2-P3, P3-P4...) (mm):",
	default_level_count="50",
	default_height12="4500",
	default_height23="4500",
	default_typical_height="3000",
	default_underground_count="3",
	default_height_p1_to_l1="3000",
	default_typical_depth="3000",
	ok_text="OK",
	cancel_text="Cancel",
	width=520,
	height=520,
):
	_ensure_wpf()
	result = _DIALOGS.LevelSetupInputs(
		title,
		level_count_label,
		height12_label,
		height23_label,
		typical_height_label,
		underground_count_label,
		height_p1_to_l1_label,
		typical_depth_label,
		default_level_count or "",
		default_height12 or "",
		default_height23 or "",
		default_typical_height or "",
		default_underground_count or "",
		default_height_p1_to_l1 or "",
		default_typical_depth or "",
		ok_text or "OK",
		cancel_text or "Cancel",
		int(width),
		int(height),
	)
	if result is None:
		return None
	return {
		"level_count": result.LevelCount or "",
		"height12": result.Height12 or "",
		"height23": result.Height23 or "",
		"typical_height": result.TypicalHeight or "",
		"underground_count": result.UndergroundCount or "",
		"height_p1_to_l1": result.HeightP1ToL1 or "",
		"typical_depth": result.TypicalDepth or "",
		"start_from_zero": bool(result.StartFromZero),
	}


def uiUtils_match_property_select_params(
	source_info,
	target_info,
	param_names,
	param_values,
	param_writable,
	width=860,
	height=700,
):
	"""Show the Match Property parameter selection dialog.

	Returns a list of selected parameter names, or None if cancelled.
	"""
	_ensure_wpf()
	from System.Collections.Generic import List as NetList
	from System import Boolean

	names_list = _to_net_string_list(param_names) or NetList[String]()
	values_list = _to_net_string_list(param_values) or NetList[String]()

	writable_list = NetList[Boolean]()
	if param_writable:
		for b in param_writable:
			writable_list.Add(bool(b))

	result = _DIALOGS.MatchPropertySelectParams(
		str(source_info or ""),
		str(target_info or ""),
		names_list,
		values_list,
		writable_list,
	)
	if result is None:
		return None
	return list(result.SelectedParams) if result.SelectedParams else []
