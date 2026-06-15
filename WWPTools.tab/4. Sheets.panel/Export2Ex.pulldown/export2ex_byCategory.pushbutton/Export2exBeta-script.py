import json
import os
import re
import sys
import time
import traceback

import clr
clr.AddReference('System.Xml')
clr.AddReference('PresentationCore')
clr.AddReference('PresentationFramework')
from System import String
from System import Object
from System.Collections.Generic import List
from System.IO import File
from System.Windows.Media import Brushes
from System.Windows.Controls import ListBoxItem

from pyrevit import DB
from WWP_settings import get_tool_settings
from WWP_versioning import apply_window_title


CONFIG_LAST_EXCEL_PATH = "last_excel_path"
CONFIG_LAST_MODE = "last_mode"
CONFIG_LAST_SCHEDULE_ID = "last_schedule_id"
CONFIG_LAST_CATEGORY_ID = "last_category_id"
CONFIG_LAST_PARAM_NAMES = "last_param_names"
CONFIG_LAST_SHEET_NAME = "last_sheet_name"
PARAM_SAVED_SETS = "! P_STATS_Export_Text"
SAVED_SET_NAMESPACE = "export2ex_beta"
LOG_FILE_NAME = "Export2ExBeta.log"
ALLOWED_EXCEL_EXTENSIONS = (".xlsx", ".xlsm")
MODE_FROM_SCHEDULE = "schedule"
MODE_BY_CATEGORY = "category"
EMBEDDED_EXPORT_DIALOG_XAML = r'''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Export2Ex - By Category"
        Height="820"
        Width="1000"
        MinHeight="640"
        MinWidth="800"
        WindowStartupLocation="CenterScreen"
        ResizeMode="CanResizeWithGrip"
        FontFamily="Segoe UI"
        FontSize="14"
        Background="#F3F5F7">
    <Window.Resources>
        <Style x:Key="PrimaryButtonStyle" TargetType="Button">
            <Setter Property="Background" Value="#3F9AD9"/>
            <Setter Property="Foreground" Value="White"/>
            <Setter Property="BorderBrush" Value="#3F9AD9"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Height" Value="30"/>
            <Setter Property="Padding" Value="18,0"/>
            <Setter Property="FontWeight" Value="SemiBold"/>
        </Style>
        <Style x:Key="SecondaryButtonStyle" TargetType="Button" BasedOn="{StaticResource PrimaryButtonStyle}">
            <Setter Property="Background" Value="#FFFFFF"/>
            <Setter Property="Foreground" Value="#1F2937"/>
            <Setter Property="BorderBrush" Value="#CBD5E1"/>
        </Style>
        <Style TargetType="TextBox">
            <Setter Property="Background" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#CBD5E1"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="6,4"/>
            <Setter Property="MinHeight" Value="28"/>
        </Style>
        <Style TargetType="ListBox">
            <Setter Property="Background" Value="#FFFFFF"/>
            <Setter Property="BorderBrush" Value="#CBD5E1"/>
            <Setter Property="BorderThickness" Value="1"/>
            <Setter Property="Padding" Value="2"/>
        </Style>
    </Window.Resources>
    <Grid Margin="16">
        <Border Background="#FFFFFF"
                BorderBrush="#D7DEE6"
                BorderThickness="1"
                Padding="20">
            <Grid>
                <Grid.RowDefinitions>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="Auto"/>
                    <RowDefinition Height="*"/>
                    <RowDefinition Height="Auto"/>
                </Grid.RowDefinitions>

                <!-- Row 0: Saved sets -->
                <Grid Grid.Row="0" Margin="0,0,0,8">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="Auto"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0"
                               Text="Saved set:"
                               Foreground="#374151"
                               VerticalAlignment="Center"
                               FontSize="12"
                               Margin="0,0,8,0"/>
                    <ComboBox Name="SavedSetBox"
                              Grid.Column="1"
                              IsEditable="True"
                              Margin="0,0,8,0"/>
                    <Button Name="LoadSetButton"
                            Grid.Column="2"
                            Content="Load"
                            Width="64"
                            Margin="0,0,6,0"
                            Style="{StaticResource SecondaryButtonStyle}"/>
                    <Button Name="SaveSetButton"
                            Grid.Column="3"
                            Content="Save"
                            Width="64"
                            Margin="0,0,6,0"
                            Style="{StaticResource SecondaryButtonStyle}"/>
                    <Button Name="DeleteSetButton"
                            Grid.Column="4"
                            Content="Delete"
                            Width="64"
                            Style="{StaticResource SecondaryButtonStyle}"/>
                </Grid>

                <!-- Row 1: Excel path + Browse -->
                <Grid Grid.Row="1" Margin="0,0,0,8">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="90"/>
                    </Grid.ColumnDefinitions>
                    <TextBox Name="ExcelPath" Margin="0,0,8,0"/>
                    <Button Name="BrowseExcel"
                            Grid.Column="1"
                            Content="Browse"
                            Style="{StaticResource SecondaryButtonStyle}"/>
                </Grid>

                <!-- Row 2: Sheet name -->
                <Grid Grid.Row="2" Margin="0,0,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="Auto"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>
                    <TextBlock Grid.Column="0"
                               Text="Sheet name:"
                               Foreground="#374151"
                               VerticalAlignment="Center"
                               FontSize="12"
                               Margin="0,0,8,0"/>
                    <TextBox Name="SheetNameBox" Grid.Column="1"/>
                </Grid>

                <!-- Row 3: Mode toggle + Units selector -->
                <Border Grid.Row="3"
                        BorderBrush="#E2E8F0"
                        BorderThickness="0,1,0,0"
                        Margin="0,12,0,0"
                        Padding="0,10,0,0">
                    <DockPanel>
                        <StackPanel DockPanel.Dock="Right"
                                    Orientation="Horizontal"
                                    VerticalAlignment="Center">
                            <TextBlock Text="Units:"
                                       Foreground="#6B7280"
                                       FontSize="12"
                                       VerticalAlignment="Center"
                                       Margin="0,0,6,0"/>
                            <ComboBox Name="UnitsBox"
                                      Width="150"
                                      FontSize="12"
                                      SelectedIndex="0">
                                <ComboBoxItem Content="Project units"/>
                                <ComboBoxItem Content="Imperial"/>
                                <ComboBoxItem Content="Metric"/>
                            </ComboBox>
                        </StackPanel>
                        <CheckBox Name="FromScheduleToggle"
                                  Content="Category From Schedule"
                                  FontSize="13"
                                  FontWeight="SemiBold"
                                  Foreground="#374151"
                                  VerticalAlignment="Center"/>
                    </DockPanel>
                </Border>

                <!-- Row 4: Source search + list -->
                <StackPanel Grid.Row="4" Margin="0,10,0,0">
                    <TextBlock Name="SourceLabel"
                               Text="Search categories:"
                               Foreground="#6B7280"
                               FontSize="12"
                               Margin="0,0,0,2"/>
                    <TextBox Name="SourceSearchBox" Margin="0,0,0,4"/>
                    <TextBlock Name="SourceListLabel"
                               Text="Categories"
                               Foreground="#6B7280"
                               FontSize="12"
                               Margin="0,0,0,2"/>
                    <ListBox Name="SourceList"
                             Height="150"
                             SelectionMode="Single"/>
                </StackPanel>

                <!-- Row 5: Available Parameters | buttons | Selected Properties -->
                <Grid Grid.Row="5" Margin="0,10,0,0">
                    <Grid.ColumnDefinitions>
                        <ColumnDefinition Width="*"/>
                        <ColumnDefinition Width="130"/>
                        <ColumnDefinition Width="*"/>
                    </Grid.ColumnDefinitions>

                    <!-- LEFT: Available Parameters -->
                    <Grid Grid.Column="0">
                        <Grid.RowDefinitions>
                            <RowDefinition Height="Auto"/>
                            <RowDefinition Height="Auto"/>
                            <RowDefinition Height="*"/>
                        </Grid.RowDefinitions>
                        <TextBlock Grid.Row="0"
                                   Text="Available Parameters:"
                                   Foreground="#374151"
                                   FontWeight="SemiBold"
                                   Margin="0,0,0,4"/>
                        <WrapPanel Grid.Row="1" Margin="0,0,0,4">
                            <CheckBox Name="ShowReadOnlyFilter"
                                      Content="Show Read-Only"
                                      IsChecked="True"
                                      FontSize="12"
                                      Foreground="#374151"
                                      VerticalAlignment="Center"
                                      Margin="0,0,12,0"/>
                            <TextBlock Text="Search:"
                                       Foreground="#6B7280"
                                       FontSize="12"
                                       VerticalAlignment="Center"
                                       Margin="0,0,4,0"/>
                            <TextBox Name="ParameterSearchBox" Width="120" FontSize="12"/>
                        </WrapPanel>
                        <ListBox Name="ParameterList"
                                 Grid.Row="2"
                                 SelectionMode="Extended"
                                 MinHeight="80"/>
                    </Grid>

                    <!-- MIDDLE: Add/Remove + Move buttons -->
                    <StackPanel Grid.Column="1"
                                Margin="6,30,6,0"
                                VerticalAlignment="Top">
                        <Button Name="AddParameterButton"
                                Content="Add -->"
                                Margin="0,0,0,6"
                                Style="{StaticResource SecondaryButtonStyle}"/>
                        <Button Name="RemoveParameterButton"
                                Content="&lt;-- Remove"
                                Style="{StaticResource SecondaryButtonStyle}"/>
                        <Rectangle Height="1" Fill="#E2E8F0" Margin="0,12,0,10"/>
                        <Button Name="MoveParameterUpButton"
                                Content="Move Up"
                                Margin="0,0,0,6"
                                Style="{StaticResource SecondaryButtonStyle}"/>
                        <Button Name="MoveParameterDownButton"
                                Content="Move Down"
                                Style="{StaticResource SecondaryButtonStyle}"/>
                    </StackPanel>

                    <!-- RIGHT: Selected Properties -->
                    <Grid Grid.Column="2">
                        <Grid.RowDefinitions>
                            <RowDefinition Height="Auto"/>
                            <RowDefinition Height="*"/>
                        </Grid.RowDefinitions>
                        <TextBlock Grid.Row="0"
                                   Text="Selected Properties:"
                                   Foreground="#374151"
                                   FontWeight="SemiBold"
                                   Margin="0,0,0,4"/>
                        <ListBox Name="SelectedParameterList"
                                 Grid.Row="1"
                                 SelectionMode="Extended"
                                 MinHeight="80"/>
                    </Grid>
                </Grid>

                <!-- Row 6: Footer -->
                <DockPanel Grid.Row="6" Margin="0,16,0,0">
                    <Image Name="LogoImage"
                           DockPanel.Dock="Left"
                           Width="56"
                           Height="56"
                           VerticalAlignment="Bottom"
                           HorizontalAlignment="Left"
                           IsHitTestVisible="False"/>
                    <StackPanel DockPanel.Dock="Right"
                                Orientation="Horizontal"
                                HorizontalAlignment="Right">
                        <Button Name="BatchExportButton"
                                Style="{StaticResource SecondaryButtonStyle}"
                                Width="150"
                                Margin="0,0,8,0"
                                Content="Batch Export..."/>
                        <Button Name="OkButton"
                                Style="{StaticResource PrimaryButtonStyle}"
                                Width="150"
                                Margin="0,0,8,0"
                                Content="Export"/>
                        <Button Name="CancelButton"
                                Style="{StaticResource SecondaryButtonStyle}"
                                Width="150"
                                Content="Cancel"/>
                    </StackPanel>
                </DockPanel>
            </Grid>
        </Border>
    </Grid>
</Window>'''




def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-

def sanitize_sheet_name(name):
    safe = re.sub(r"[:\\/?*\[\]]", "_", (name or "").strip())
    return (safe or "Schedule")[:31]


def _pluralize(name):
    if not name:
        return name
    parts = name.rsplit(" ", 1)
    last = parts[-1]
    lower_last = last.lower()
    if lower_last.endswith("s"):
        return name
    if lower_last.endswith(("x", "z", "ch", "sh")):
        last = last + "es"
    else:
        last = last + "s"
    parts[-1] = last
    return " ".join(parts)


def default_sheet_name_for_category(category_name):
    return sanitize_sheet_name(_pluralize(category_name or "Category Export"))


def normalize_excel_output_path(path, default_ext=".xlsx"):
    value = (path or "").strip()
    if not value:
        return ""
    root, ext = os.path.splitext(value)
    if not ext:
        return value + default_ext
    if ext.lower() in ALLOWED_EXCEL_EXTENSIONS:
        return value
    return ""


def element_id_value(elem_id):
    if elem_id is None:
        return -1
    if hasattr(elem_id, "IntegerValue"):
        return _elem_id_int(elem_id)
    if hasattr(elem_id, "Value"):
        return elem_id.Value
    try:
        return int(elem_id)
    except Exception:
        return -1


def get_active_doc():
    try:
        uidoc = __revit__.ActiveUIDocument
        if uidoc:
            return uidoc.Document
    except Exception:
        pass
    return None


def read_saved_sets(doc):
    try:
        proj_info = doc.ProjectInformation
        if proj_info is None:
            return {}
        param = proj_info.LookupParameter(PARAM_SAVED_SETS)
        if param is None:
            return {}
        raw = (param.AsString() or "").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        if isinstance(data.get(SAVED_SET_NAMESPACE), dict):
            return data.get(SAVED_SET_NAMESPACE) or {}
        if any(k in data for k in _ALL_NAMESPACES):
            return {}
        return data
    except Exception:
        return {}


_ALL_NAMESPACES = ("export2ex", "export2ex_beta", "mass_stats")


def _looks_like_legacy_saved_sets(data):
    if not isinstance(data, dict) or not data:
        return False
    if any(k in data for k in _ALL_NAMESPACES):
        return False
    return all(isinstance(v, dict) for v in data.values())


def write_saved_sets(doc, sets_dict):
    try:
        proj_info = doc.ProjectInformation
        if proj_info is None:
            return False
        param = proj_info.LookupParameter(PARAM_SAVED_SETS)
        t = DB.Transaction(doc, "Save Export2Ex Settings")
        t.Start()
        try:
            if param is None:
                app = doc.Application
                cat_set = app.Create.NewCategorySet()
                pi_cat = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_ProjectInformation)
                if pi_cat is None:
                    t.RollBack()
                    return False
                cat_set.Insert(pi_cat)
                binding = app.Create.NewInstanceBinding(cat_set)
                from Autodesk.Revit.DB import GroupTypeId
                opts = DB.InternalDefinitionCreationOptions(PARAM_SAVED_SETS, DB.SpecTypeId.String.Text)
                opts.Visible = True
                doc.ParameterBindings.Insert(opts, binding, GroupTypeId.Data)
                doc.Regenerate()
                proj_info = doc.ProjectInformation
                param = proj_info.LookupParameter(PARAM_SAVED_SETS)
            if param is None or param.IsReadOnly:
                t.RollBack()
                return False
            raw = (param.AsString() or "").strip()
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            if _looks_like_legacy_saved_sets(payload):
                payload = {SAVED_SET_NAMESPACE: payload}
            payload[SAVED_SET_NAMESPACE] = sets_dict
            param.Set(json.dumps(payload, ensure_ascii=False, indent=2))
            t.Commit()
            return True
        except Exception as inner:
            try:
                t.RollBack()
            except Exception:
                pass
            log_exception("write_saved_sets transaction", inner)
            return False
    except Exception as exc:
        log_exception("write_saved_sets", exc)
        return False


def _log_file_path():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "pyRevit", "WWPTools", "Logs", LOG_FILE_NAME)


def log_message(message):
    try:
        log_path = _log_file_path()
        folder = os.path.dirname(log_path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as fp:
            fp.write("[{}] {}\n".format(timestamp, message))
    except Exception:
        pass


def log_exception(context, exc):
    try:
        detail = traceback.format_exc()
    except Exception:
        detail = str(exc)
    log_message("{}: {}\n{}".format(context, str(exc), detail))


def add_lib_path():
    lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))
    if lib_path not in sys.path:
        sys.path.append(lib_path)


def load_uiutils():
    add_lib_path()
    import WWP_uiUtils as ui
    return ui


def get_config_and_saver():
    return get_tool_settings("Export2ExBeta", doc=get_active_doc())


def config_get(config, name, default=None):
    try:
        value = getattr(config, name)
    except Exception:
        return default
    return default if value is None else value


def _coerce_int(value, default=None):
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        for item in value:
            coerced = _coerce_int(item, None)
            if coerced is not None:
                return coerced
        return default
    try:
        return int(value)
    except Exception:
        pass
    text = str(value).strip()
    if not text:
        return default
    match = re.search(r"-?\d+", text)
    if not match:
        return default
    try:
        return int(match.group(0))
    except Exception:
        return default


def _coerce_string_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_mode(value):
    return MODE_BY_CATEGORY if value == MODE_BY_CATEGORY else MODE_FROM_SCHEDULE


def _pick_save_file(title, filter_text, default_extension, initial_directory, file_name):
    clr.AddReference("PresentationFramework")
    from Microsoft.Win32 import SaveFileDialog

    dialog = SaveFileDialog()
    dialog.Title = title or "Save File"
    dialog.Filter = filter_text or "All files (*.*)|*.*"
    if default_extension:
        dialog.DefaultExt = default_extension
        dialog.AddExtension = True
    if initial_directory and os.path.isdir(initial_directory):
        dialog.InitialDirectory = initial_directory
    if file_name:
        dialog.FileName = file_name
    result = dialog.ShowDialog()
    if result:
        return dialog.FileName
    return None


def get_default_dir(doc):
    if doc.IsWorkshared:
        try:
            central = doc.GetWorksharingCentralModelPath()
            if central:
                return os.path.dirname(DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(central))
        except Exception:
            pass
    if doc.PathName:
        return os.path.dirname(doc.PathName)
    return os.path.expanduser("~")


def ensure_existing_dir(path, fallback=""):
    if path and os.path.isdir(path):
        return path
    if fallback and os.path.isdir(fallback):
        return fallback
    return ""


def collect_schedules(doc):
    schedules = []
    for view in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if view.IsTemplate or view.IsTitleblockRevisionSchedule:
            continue
        schedules.append(view)
    schedules.sort(key=lambda item: item.Name)
    return schedules


def _get_linked_docs(doc):
    result = []
    try:
        for link in DB.FilteredElementCollector(doc).OfClass(DB.RevitLinkInstance):
            link_doc = link.GetLinkDocument()
            if link_doc is not None:
                result.append(link_doc)
    except Exception:
        pass
    return result


def collect_category_records(doc):
    records = {}

    def _scan_doc(scan_doc):
        collector = DB.FilteredElementCollector(scan_doc).WhereElementIsNotElementType()
        for element in collector:
            try:
                category = element.Category
            except Exception:
                category = None
            if category is None:
                continue
            name = (category.Name or "").strip()
            if not name:
                continue
            category_id = element_id_value(category.Id)
            if category_id == -1:
                continue
            record = records.get(category_id)
            if record is None:
                record = {
                    "id": category.Id,
                    "id_value": category_id,
                    "name": name,
                    "count": 0,
                }
                records[category_id] = record
            record["count"] += 1

    _scan_doc(doc)
    for link_doc in _get_linked_docs(doc):
        _scan_doc(link_doc)

    result = list(records.values())
    result.sort(key=lambda item: item["name"].lower())
    return result


def get_elements_by_category(doc, category_id):
    def _collect(src_doc):
        try:
            collector = (
                DB.FilteredElementCollector(src_doc)
                .WherePasses(DB.ElementCategoryFilter(category_id))
                .WhereElementIsNotElementType()
            )
            return [e for e in collector.ToElements() if e is not None]
        except Exception:
            return []

    elements = _collect(doc)
    for link_doc in _get_linked_docs(doc):
        elements.extend(_collect(link_doc))
    return elements


def get_schedule_category_id(schedule):
    try:
        category_id = schedule.Definition.CategoryId
    except Exception:
        category_id = None
    if category_id is None or element_id_value(category_id) == -1:
        return None
    return category_id


def iter_parameter_names(element):
    if element is None:
        return
    for param in getattr(element, "Parameters", []):
        try:
            definition = param.Definition
            name = definition.Name if definition else None
        except Exception:
            name = None
        if name:
            yield name


def _iter_element_parameters(element):
    if element is None:
        return
    for param in getattr(element, "Parameters", []):
        try:
            definition = param.Definition
            name = definition.Name if definition else None
        except Exception:
            name = None
        if name:
            yield name, param


def get_parameter_options_for_category(doc, category_id, sample_limit=400):
    options = {}
    seen_types = set()
    for index, element in enumerate(get_elements_by_category(doc, category_id)):
        if index >= sample_limit:
            break
        for name, param in _iter_element_parameters(element):
            record = options.setdefault(name, {"name": name, "editable": False})
            try:
                if param is not None and not param.IsReadOnly:
                    record["editable"] = True
            except Exception:
                pass
        elem_type = get_element_type(doc, element)
        if elem_type is None:
            continue
        type_id = element_id_value(elem_type.Id)
        if type_id in seen_types:
            continue
        seen_types.add(type_id)
        for name, param in _iter_element_parameters(elem_type):
            record = options.setdefault(name, {"name": name, "editable": False})
            try:
                if param is not None and not param.IsReadOnly:
                    record["editable"] = True
            except Exception:
                pass
    try:
        proj_info = doc.ProjectInformation
        if proj_info is not None:
            for name, param in _iter_element_parameters(proj_info):
                record = options.setdefault(name, {"name": name, "editable": False})
                try:
                    if param is not None and not param.IsReadOnly:
                        record["editable"] = True
                except Exception:
                    pass
    except Exception:
        pass
    result = list(options.values())
    result.sort(key=lambda item: item["name"].lower())
    return result


class ScheduleItem(object):
    def __init__(self, view):
        self.view = view
        self.id_value = element_id_value(view.Id)
        self.category_id = get_schedule_category_id(view)
        self.display_name = "{} [id:{}]".format(view.Name, self.id_value).replace("_", "__")

    def __str__(self):
        return self.display_name


class CategoryItem(object):
    def __init__(self, record):
        self.record = record
        self.id_value = record["id_value"]
        self.display_name = "{} ({})".format(record["name"], record["count"]).replace("_", "__")

    def __str__(self):
        return self.display_name


def _to_net_list(values):
    result = List[String]()
    for value in values:
        result.Add("" if value is None else str(value))
    return result


def _to_net_object_list(values):
    result = List[Object]()
    for value in values:
        result.Add(value)
    return result


def _parameter_label(option):
    if option.get("editable", False):
        return option.get("name", "")
    return "{} (read-only)".format(option.get("name", ""))


def _make_parameter_list_item(option):
    item = ListBoxItem()
    item.Content = _parameter_label(option)
    item.Tag = option
    if not option.get("editable", False):
        item.ToolTip = "This parameter is read-only in the sampled category elements/types."
    return item


def _get_parameter_name_from_item(item):
    if item is None:
        return ""
    try:
        tag = item.Tag
    except Exception:
        tag = None
    if isinstance(tag, dict):
        return tag.get("name", "")
    return str(item)


def _load_export_window():
    from System.IO import StringReader
    from System.Windows.Markup import XamlReader
    from System.Xml import XmlReader

    xaml_path = os.path.join(os.path.dirname(__file__), "ExportSchedulesDialog.xaml")
    sources = [
        ("file", lambda: File.ReadAllText(xaml_path)),
        ("embedded", lambda: EMBEDDED_EXPORT_DIALOG_XAML),
    ]
    last_exc = None
    for source_name, source_loader in sources:
        try:
            xaml_text = source_loader()
            reader = XmlReader.Create(StringReader(xaml_text))
            window = XamlReader.Load(reader)
            if source_name != "file":
                log_message("Loaded Export2Ex Beta dialog from {} fallback".format(source_name))
            return window
        except Exception as exc:
            last_exc = exc
            log_exception("Failed to load Export2Ex Beta dialog from {}".format(source_name), exc)
    raise last_exc


def _show_beta_batch_dialog(saved_sets, doc, ui=None, add_callback=None, edit_callback=None):
    """Primary UI for Export2Ex Beta: manage and batch-export saved sets."""
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System.Windows import (Window, WindowStartupLocation, Thickness,
                                 HorizontalAlignment, VerticalAlignment,
                                 FontWeights, TextTrimming, GridLength, GridUnitType,
                                 ResizeMode)
    from System.Windows.Controls import (Grid, StackPanel, ScrollViewer, Button,
                                          CheckBox, TextBlock, TextBox, ColumnDefinition,
                                          RowDefinition, Orientation, ScrollBarVisibility)

    paths = {}
    _ok_clicked = [False]
    checkboxes = {}
    path_labels = {}
    sheet_boxes = {}
    data_row_elements = []

    window = Window()
    window.Title = "Export2Ex Beta"
    window.Width = 820
    window.MinWidth = 560
    window.Height = 500
    window.MinHeight = 300
    window.ResizeMode = ResizeMode.CanResizeWithGrip
    window.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = Grid()
    outer.Margin = Thickness(12)
    rh = RowDefinition(); rh.Height = GridLength.Auto
    rc = RowDefinition()
    rb = RowDefinition(); rb.Height = GridLength.Auto
    outer.RowDefinitions.Add(rh)
    outer.RowDefinitions.Add(rc)
    outer.RowDefinitions.Add(rb)
    window.Content = outer

    prompt = TextBlock()
    prompt.Text = "Manage export sets below. Select sets and click Export Selected to run."
    prompt.Margin = Thickness(0, 0, 0, 8)
    Grid.SetRow(prompt, 0)
    outer.Children.Add(prompt)

    scroll = ScrollViewer()
    scroll.HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled
    scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    Grid.SetRow(scroll, 1)
    outer.Children.Add(scroll)

    tbl = Grid()
    for w in [28, 160, 150, -1, 34, 60]:
        cd = ColumnDefinition()
        cd.Width = GridLength(1, GridUnitType.Star) if w == -1 else GridLength(w)
        tbl.ColumnDefinitions.Add(cd)
    scroll.Content = tbl

    hrd = RowDefinition(); hrd.Height = GridLength.Auto
    tbl.RowDefinitions.Add(hrd)
    for col, text in [(1, "Set Name"), (2, "Sheet Name"), (3, "File Path")]:
        tb = TextBlock()
        tb.Text = text
        tb.FontWeight = FontWeights.Bold
        tb.Margin = Thickness(4, 2, 4, 6)
        Grid.SetRow(tb, 0)
        Grid.SetColumn(tb, col)
        tbl.Children.Add(tb)

    def _make_edit_row(sname_):
        def _on_edit_row(_s, _e):
            sdata = saved_sets.get(sname_) or {}
            if edit_callback:
                edit_callback(sname_, sdata)
            _refresh_from_project()
        return _on_edit_row

    def _make_browse(sname_):
        def _on_browse(_s, _e):
            cur = paths.get(sname_) or ""
            init_dir = os.path.dirname(cur) if cur else get_default_dir(doc)
            safe = re.sub(r'[\\/:*?"<>|]', "_", sname_)
            fname = os.path.basename(cur) if cur else "{}.xlsx".format(safe)
            new_path = _pick_save_file(
                title="'{}' -- Choose Output File".format(sname_),
                filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                default_extension="xlsx",
                initial_directory=init_dir,
                file_name=fname,
            )
            new_path = normalize_excel_output_path(new_path or "")
            if new_path:
                paths[sname_] = new_path
                path_labels[sname_].Text = new_path
        return _on_browse

    def _rebuild_rows():
        for elems in data_row_elements:
            for elem in elems:
                tbl.Children.Remove(elem)
        data_row_elements[:] = []
        while tbl.RowDefinitions.Count > 1:
            tbl.RowDefinitions.RemoveAt(1)
        checkboxes.clear()
        path_labels.clear()
        sheet_boxes.clear()

        current_names = sorted(saved_sets.keys())
        if not current_names:
            rd = RowDefinition(); rd.Height = GridLength.Auto
            tbl.RowDefinitions.Add(rd)
            empty_tb = TextBlock()
            empty_tb.Text = "No saved sets. Click 'Add Set' to create one."
            empty_tb.Margin = Thickness(4, 16, 4, 4)
            empty_tb.HorizontalAlignment = HorizontalAlignment.Center
            Grid.SetRow(empty_tb, 1)
            Grid.SetColumnSpan(empty_tb, 6)
            tbl.Children.Add(empty_tb)
            data_row_elements.append([empty_tb])
            return

        for row_idx, sname in enumerate(current_names, 1):
            sdata = saved_sets.get(sname) or {}
            if sname not in paths:
                paths[sname] = (sdata.get("excel_path") or "").strip()
            saved_sheet = (sdata.get("sheet_name") or sanitize_sheet_name(sname)).strip()

            rd = RowDefinition(); rd.Height = GridLength(34)
            tbl.RowDefinitions.Add(rd)
            row_elems = []

            cb = CheckBox()
            cb.IsChecked = True
            cb.VerticalAlignment = VerticalAlignment.Center
            cb.HorizontalAlignment = HorizontalAlignment.Center
            Grid.SetRow(cb, row_idx)
            Grid.SetColumn(cb, 0)
            tbl.Children.Add(cb)
            checkboxes[sname] = cb
            row_elems.append(cb)

            name_tb = TextBlock()
            name_tb.Text = sname
            name_tb.VerticalAlignment = VerticalAlignment.Center
            name_tb.Margin = Thickness(4, 0, 8, 0)
            name_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetRow(name_tb, row_idx)
            Grid.SetColumn(name_tb, 1)
            tbl.Children.Add(name_tb)
            row_elems.append(name_tb)

            sheet_tb = TextBox()
            sheet_tb.Text = saved_sheet
            sheet_tb.VerticalAlignment = VerticalAlignment.Center
            sheet_tb.Margin = Thickness(4, 3, 8, 3)
            Grid.SetRow(sheet_tb, row_idx)
            Grid.SetColumn(sheet_tb, 2)
            tbl.Children.Add(sheet_tb)
            sheet_boxes[sname] = sheet_tb
            row_elems.append(sheet_tb)

            path_tb = TextBlock()
            path_tb.Text = paths[sname] or "(no path)"
            path_tb.VerticalAlignment = VerticalAlignment.Center
            path_tb.Margin = Thickness(4, 0, 4, 0)
            path_tb.TextTrimming = TextTrimming.CharacterEllipsis
            Grid.SetRow(path_tb, row_idx)
            Grid.SetColumn(path_tb, 3)
            tbl.Children.Add(path_tb)
            path_labels[sname] = path_tb
            row_elems.append(path_tb)

            btn = Button()
            btn.Content = "..."
            btn.Margin = Thickness(2, 3, 2, 3)
            Grid.SetRow(btn, row_idx)
            Grid.SetColumn(btn, 4)
            tbl.Children.Add(btn)
            btn.Click += _make_browse(sname)
            row_elems.append(btn)

            edit_row_btn = Button()
            edit_row_btn.Content = "Edit"
            edit_row_btn.Margin = Thickness(2, 3, 2, 3)
            Grid.SetRow(edit_row_btn, row_idx)
            Grid.SetColumn(edit_row_btn, 5)
            tbl.Children.Add(edit_row_btn)
            edit_row_btn.Click += _make_edit_row(sname)
            row_elems.append(edit_row_btn)

            data_row_elements.append(row_elems)

    _rebuild_rows()

    btns = StackPanel()
    btns.Orientation = Orientation.Horizontal
    btns.HorizontalAlignment = HorizontalAlignment.Right
    btns.Margin = Thickness(0, 10, 0, 0)
    Grid.SetRow(btns, 2)
    outer.Children.Add(btns)

    add_btn = Button()
    add_btn.Content = "Add Set"
    add_btn.MinWidth = 80
    add_btn.Margin = Thickness(0, 0, 6, 0)

    del_btn = Button()
    del_btn.Content = "Delete"
    del_btn.MinWidth = 70
    del_btn.Margin = Thickness(0, 0, 20, 0)

    ok_btn = Button()
    ok_btn.Content = "Export Selected"
    ok_btn.MinWidth = 110
    ok_btn.Margin = Thickness(0, 0, 6, 0)

    cancel_btn = Button()
    cancel_btn.Content = "Cancel"
    cancel_btn.MinWidth = 70

    def _refresh_from_project():
        new_sets = read_saved_sets(doc)
        saved_sets.clear()
        saved_sets.update(new_sets)
        _rebuild_rows()

    def _add(_s, _e):
        if add_callback:
            add_callback()
        _refresh_from_project()

    def _delete(_s, _e):
        to_delete = [sname for sname, cb in list(checkboxes.items()) if cb.IsChecked]
        if not to_delete:
            if ui:
                ui.uiUtils_alert("Check the sets you want to delete.", title="Export2Ex Beta")
            return
        for sname in to_delete:
            if sname in saved_sets:
                del saved_sets[sname]
            if sname in paths:
                del paths[sname]
        write_saved_sets(doc, saved_sets)
        _rebuild_rows()

    def _ok(_s, _e):
        _ok_clicked[0] = True
        window.Close()

    def _cancel(_s, _e):
        window.Close()

    add_btn.Click += _add
    del_btn.Click += _delete
    ok_btn.Click += _ok
    cancel_btn.Click += _cancel

    btns.Children.Add(add_btn)
    btns.Children.Add(del_btn)
    btns.Children.Add(ok_btn)
    btns.Children.Add(cancel_btn)

    window.ShowDialog()

    if not _ok_clicked[0]:
        return None

    selected = [sname for sname in sorted(saved_sets.keys()) if sname in checkboxes and checkboxes[sname].IsChecked]
    final_sheet_names = {sname: (sheet_boxes[sname].Text or "").strip() for sname in sorted(saved_sets.keys()) if sname in sheet_boxes}
    return selected, dict(paths), final_sheet_names


def show_export_form(ui, doc, schedules, categories, init_excel_path, initial_mode, initial_source_id, initial_category_id, initial_param_names, initial_sheet_name="", initial_set_name=""):
    clr.AddReference("PresentationFramework")
    clr.AddReference("PresentationCore")
    clr.AddReference("WindowsBase")
    from System import Uri
    from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage

    window = _load_export_window()
    apply_window_title(window, "Export2Ex Beta")

    from_schedule_toggle = window.FindName("FromScheduleToggle")
    units_box = window.FindName("UnitsBox")
    source_search_box = window.FindName("SourceSearchBox")
    source_label = window.FindName("SourceLabel")
    source_list_label = window.FindName("SourceListLabel")
    source_list = window.FindName("SourceList")
    parameter_search_box = window.FindName("ParameterSearchBox")
    parameter_list = window.FindName("ParameterList")
    selected_parameter_list = window.FindName("SelectedParameterList")
    add_parameter_button = window.FindName("AddParameterButton")
    remove_parameter_button = window.FindName("RemoveParameterButton")
    move_parameter_up_button = window.FindName("MoveParameterUpButton")
    move_parameter_down_button = window.FindName("MoveParameterDownButton")
    excel_path = window.FindName("ExcelPath")
    browse_excel = window.FindName("BrowseExcel")
    ok_button = window.FindName("OkButton")
    cancel_button = window.FindName("CancelButton")
    batch_export_button = window.FindName("BatchExportButton")
    logo_image = window.FindName("LogoImage")
    show_read_only_filter = window.FindName("ShowReadOnlyFilter")
    sheet_name_box = window.FindName("SheetNameBox")
    saved_set_box = window.FindName("SavedSetBox")
    load_set_button = window.FindName("LoadSetButton")
    save_set_button = window.FindName("SaveSetButton")
    delete_set_button = window.FindName("DeleteSetButton")

    excel_path.Text = init_excel_path or ""
    schedule_items = schedules or []
    category_items = categories or []
    parameter_cache = {}
    selected_params_by_category = {}
    initial_category_id = _coerce_int(initial_category_id, None)
    initial_source_id = _coerce_int(initial_source_id, None)
    initial_param_names = _coerce_string_list(initial_param_names)
    if initial_category_id is not None and initial_param_names:
        selected_params_by_category[initial_category_id] = list(initial_param_names)

    def _current_mode():
        try:
            return MODE_FROM_SCHEDULE if from_schedule_toggle.IsChecked else MODE_BY_CATEGORY
        except Exception:
            return MODE_BY_CATEGORY

    from_schedule_toggle.IsChecked = (initial_mode == MODE_FROM_SCHEDULE)

    def _get_source_items():
        return category_items if _current_mode() == MODE_BY_CATEGORY else schedule_items

    def _resolve_category_id(item):
        if item is None:
            return None
        if _current_mode() == MODE_BY_CATEGORY:
            return item.id_value
        if item.category_id is None:
            return None
        return element_id_value(item.category_id)

    def _category_name_for_id(category_id):
        if category_id is None:
            return ""
        for item in category_items:
            if item.id_value == category_id:
                return item.record["name"]
        return ""

    def _default_sheet_name_for_item(item):
        category_id = _resolve_category_id(item)
        category_name = _category_name_for_id(category_id)
        if category_name:
            return default_sheet_name_for_category(category_name)
        if item is not None and _current_mode() == MODE_BY_CATEGORY:
            return default_sheet_name_for_category(item.record["name"])
        return default_sheet_name_for_category("Category Export")

    def _get_parameter_names(category_id):
        if category_id is None:
            return []
        if category_id not in parameter_cache:
            parameter_cache[category_id] = get_parameter_options_for_category(doc, DB.ElementId(category_id))
        return parameter_cache[category_id]

    def _get_selected_parameter_names(category_id):
        if category_id is None:
            return []
        selected = selected_params_by_category.get(category_id)
        if selected is None:
            selected = []
            selected_params_by_category[category_id] = selected
        return selected

    def _refresh_selected_parameter_list(category_id, preserve_selection=None):
        selected_names = _get_selected_parameter_names(category_id)
        selected_parameter_list.Items.Clear()
        id_item = ListBoxItem()
        id_item.Content = "Id  -- always exported"
        id_item.Tag = {"name": "__id__", "editable": False, "__permanent__": True}
        id_item.Foreground = Brushes.SteelBlue
        selected_parameter_list.Items.Add(id_item)
        option_map = dict((option["name"], option) for option in _get_parameter_names(category_id))
        for name in selected_names:
            option = option_map.get(name, {"name": name, "editable": True})
            selected_parameter_list.Items.Add(_make_parameter_list_item(option))
        preserve = list(preserve_selection or [])
        try:
            selected_parameter_list.SelectedItems.Clear()
            for item in selected_parameter_list.Items:
                if _get_parameter_name_from_item(item) in preserve:
                    selected_parameter_list.SelectedItems.Add(item)
        except Exception:
            pass

    def _refresh_parameter_list():
        selected_item = source_list.SelectedItem
        category_id = _resolve_category_id(selected_item)
        all_options = _get_parameter_names(category_id)
        selected_names = set(_get_selected_parameter_names(category_id))
        text = (parameter_search_box.Text or "").strip().lower()
        filtered = [option for option in all_options if option["name"] not in selected_names]
        try:
            if not show_read_only_filter.IsChecked:
                filtered = [option for option in filtered if option.get("editable", False)]
        except Exception:
            pass
        if text:
            filtered = [option for option in filtered if text in option["name"].lower()]
        parameter_list.Items.Clear()
        for option in filtered:
            parameter_list.Items.Add(_make_parameter_list_item(option))
        _refresh_selected_parameter_list(category_id)

    def _refresh_source_list():
        mode = _current_mode()
        items = _get_source_items()
        text = (source_search_box.Text or "").strip().lower()
        filtered = items if not text else [item for item in items if text in item.display_name.lower()]
        source_list.DisplayMemberPath = "display_name"
        source_list.ItemsSource = _to_net_object_list(filtered)
        source_label.Text = "Search categories" if mode == MODE_BY_CATEGORY else "Search schedules"
        source_list_label.Text = "Categories" if mode == MODE_BY_CATEGORY else "Schedules"
        target_id = initial_source_id
        selected = None
        for item in filtered:
            item_id = item.id_value if mode == MODE_BY_CATEGORY else item.id_value
            if target_id is not None and item_id == target_id:
                selected = item
                break
        if selected is None and filtered:
            selected = filtered[0]
        source_list.SelectedItem = selected
        _refresh_parameter_list()

    _initialized = [False]
    _sheet_name_autofill = [True]
    _sheet_name_updating = [False]
    _last_auto_sheet_name = [""]

    def _set_sheet_name_text(value):
        if sheet_name_box is None:
            return
        _sheet_name_updating[0] = True
        try:
            sheet_name_box.Text = value or ""
        finally:
            _sheet_name_updating[0] = False

    def _refresh_sheet_name_default(force=False):
        if sheet_name_box is None:
            return
        selected_item = source_list.SelectedItem
        auto_name = _default_sheet_name_for_item(selected_item)
        current = (sheet_name_box.Text or "").strip()
        if force or _sheet_name_autofill[0] or not current or current == _last_auto_sheet_name[0]:
            _last_auto_sheet_name[0] = auto_name
            _sheet_name_autofill[0] = True
            _set_sheet_name_text(auto_name)

    def _sheet_name_changed(_sender=None, _args=None):
        if _sheet_name_updating[0] or sheet_name_box is None:
            return
        current = (sheet_name_box.Text or "").strip()
        _sheet_name_autofill[0] = not current or current == _last_auto_sheet_name[0]

    def _auto_populate_schedule_fields():
        if not _initialized[0] or _current_mode() != MODE_FROM_SCHEDULE:
            return
        selected_item = source_list.SelectedItem
        if selected_item is None:
            return
        category_id = _resolve_category_id(selected_item)
        if category_id is None:
            return
        try:
            fields = get_visible_schedule_fields(selected_item.view)
        except Exception:
            return
        names = []
        for f in fields:
            try:
                name = f["field"].GetName()
                if name and name not in names:
                    names.append(name)
            except Exception:
                pass
        selected_params_by_category[category_id] = names

    def _source_selection_changed(_sender, _args):
        _auto_populate_schedule_fields()
        _refresh_parameter_list()
        _refresh_sheet_name_default()

    def _add_parameters(_sender=None, _args=None):
        selected_item = source_list.SelectedItem
        category_id = _resolve_category_id(selected_item)
        if category_id is None:
            return
        selected_names = _get_selected_parameter_names(category_id)
        for item in parameter_list.SelectedItems:
            name = _get_parameter_name_from_item(item)
            if name not in selected_names:
                selected_names.append(name)
        _refresh_parameter_list()

    def _remove_parameters(_sender=None, _args=None):
        selected_item = source_list.SelectedItem
        category_id = _resolve_category_id(selected_item)
        if category_id is None:
            return
        selected_names = _get_selected_parameter_names(category_id)
        removed = set(
            _get_parameter_name_from_item(item)
            for item in selected_parameter_list.SelectedItems
            if not (isinstance(getattr(item, "Tag", None), dict) and item.Tag.get("__permanent__"))
        )
        if not removed:
            return
        selected_params_by_category[category_id] = [name for name in selected_names if name not in removed]
        _refresh_parameter_list()

    def _move_selected_parameters(direction):
        selected_item = source_list.SelectedItem
        category_id = _resolve_category_id(selected_item)
        if category_id is None:
            return
        selected_names = _get_selected_parameter_names(category_id)
        selected_now = [_get_parameter_name_from_item(item) for item in selected_parameter_list.SelectedItems]
        if not selected_now:
            return
        indices = [idx for idx, name in enumerate(selected_names) if name in selected_now]
        if direction < 0:
            for idx in indices:
                if idx <= 0:
                    continue
                selected_names[idx - 1], selected_names[idx] = selected_names[idx], selected_names[idx - 1]
        else:
            for idx in reversed(indices):
                if idx >= len(selected_names) - 1:
                    continue
                selected_names[idx + 1], selected_names[idx] = selected_names[idx], selected_names[idx + 1]
        _refresh_selected_parameter_list(category_id, preserve_selection=selected_now)

    def _move_up(_sender=None, _args=None):
        _move_selected_parameters(-1)

    def _move_down(_sender=None, _args=None):
        _move_selected_parameters(1)

    def _browse_excel(_sender, _args):
        current = excel_path.Text or ""
        file_name = os.path.basename(current) if current else "CategoryExport.xlsx"
        initial_directory = ensure_existing_dir(
            os.path.dirname(current) if current else "",
            os.path.dirname(init_excel_path) if init_excel_path else get_default_dir(get_active_doc()),
        )
        try:
            file_path = _pick_save_file(
                title="Export Category Data to Excel",
                filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                default_extension="xlsx",
                initial_directory=initial_directory,
                file_name=file_name,
            )
        except Exception as exc:
            log_exception("Native browse Excel dialog failed", exc)
            try:
                file_path = _pick_save_file(
                    title="Export Category Data to Excel",
                    filter_text="Excel Workbook (*.xlsx;*.xlsm)|*.xlsx;*.xlsm",
                    default_extension="xlsx",
                    initial_directory="",
                    file_name=file_name,
                )
            except Exception as retry_exc:
                log_exception("Native browse Excel dialog retry failed", retry_exc)
                ui.uiUtils_alert(
                    "Could not open the Excel save dialog. Check the suggested path and try again.",
                    title="Export2Ex Beta",
                )
                return
        if file_path:
            excel_path.Text = file_path

    saved_sets = read_saved_sets(doc)

    def _refresh_saved_set_dropdown():
        if saved_set_box is None:
            return
        current_text = saved_set_box.Text or ""
        saved_set_box.Items.Clear()
        for name in sorted(saved_sets.keys()):
            saved_set_box.Items.Add(name)
        saved_set_box.Text = current_text

    def _get_current_set_data():
        selected_item = source_list.SelectedItem
        category_id = _resolve_category_id(selected_item)
        source_id = selected_item.id_value if selected_item is not None else None
        return {
            "mode": _current_mode(),
            "source_id": source_id,
            "category_id": category_id,
            "param_names": list(selected_params_by_category.get(category_id, [])),
            "sheet_name": (sheet_name_box.Text or "").strip() if sheet_name_box is not None else "",
            "excel_path": (excel_path.Text or "").strip() if excel_path is not None else "",
        }

    def _apply_saved_set(set_data):
        _initialized[0] = False
        try:
            mode = set_data.get("mode", MODE_BY_CATEGORY)
            source_id = _coerce_int(set_data.get("source_id"), None)
            category_id = _coerce_int(set_data.get("category_id"), None)
            param_names = set_data.get("param_names") or []
            saved_sheet = (set_data.get("sheet_name") or "").strip()
            saved_excel = (set_data.get("excel_path") or "").strip()
            if category_id is not None:
                selected_params_by_category[category_id] = list(param_names)
            from_schedule_toggle.IsChecked = (mode == MODE_FROM_SCHEDULE)
            source_search_box.Text = ""
            for item in _get_source_items():
                if item.id_value == source_id:
                    source_list.SelectedItem = item
                    break
            if sheet_name_box is not None and saved_sheet:
                sheet_name_box.Text = saved_sheet
            if excel_path is not None and saved_excel:
                excel_path.Text = saved_excel
            _refresh_parameter_list()
        finally:
            _initialized[0] = True

    def _load_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name or name not in saved_sets:
            ui.uiUtils_alert("Select a saved set from the dropdown to load.", title="Export2Ex Beta")
            return
        _apply_saved_set(saved_sets[name])

    def _save_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name:
            ui.uiUtils_alert("Type a name for the saved set before saving.", title="Export2Ex Beta")
            return
        saved_sets[name] = _get_current_set_data()
        if not write_saved_sets(doc, saved_sets):
            ui.uiUtils_alert(
                "Could not write to '{}' on Project Information.\nCheck the parameter exists and is not read-only.".format(PARAM_SAVED_SETS),
                title="Export2Ex Beta",
            )
            return
        _refresh_saved_set_dropdown()

    def _delete_set(_sender=None, _args=None):
        if saved_set_box is None:
            return
        name = (saved_set_box.Text or "").strip()
        if not name or name not in saved_sets:
            ui.uiUtils_alert("Select a saved set from the dropdown to delete.", title="Export2Ex Beta")
            return
        del saved_sets[name]
        if not write_saved_sets(doc, saved_sets):
            ui.uiUtils_alert(
                "Could not update '{}' on Project Information.".format(PARAM_SAVED_SETS),
                title="Export2Ex Beta",
            )
            return
        saved_set_box.Text = ""
        _refresh_saved_set_dropdown()

    _batch_result = [None]

    def _batch_export(_sender, _args):
        result = _show_beta_batch_dialog(saved_sets, doc)
        if result is None:
            return
        _batch_result[0] = result
        window.DialogResult = False
        window.Close()

    def _ok(_sender, _args):
        window.DialogResult = True
        window.Close()

    def _cancel(_sender, _args):
        window.DialogResult = False
        window.Close()

    try:
        logo_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib", "WWPtools-logo.png"))
        if logo_image is not None and os.path.isfile(logo_path):
            bitmap = BitmapImage()
            bitmap.BeginInit()
            bitmap.UriSource = Uri(logo_path)
            bitmap.CacheOption = BitmapCacheOption.OnLoad
            bitmap.EndInit()
            logo_image.Source = bitmap
    except Exception:
        pass

    from_schedule_toggle.Checked += lambda _sender, _args: _refresh_source_list()
    from_schedule_toggle.Unchecked += lambda _sender, _args: _refresh_source_list()
    source_search_box.TextChanged += lambda _sender, _args: _refresh_source_list()
    source_list.SelectionChanged += _source_selection_changed
    parameter_search_box.TextChanged += lambda _sender, _args: _refresh_parameter_list()
    show_read_only_filter.Checked += lambda _sender, _args: _refresh_parameter_list()
    show_read_only_filter.Unchecked += lambda _sender, _args: _refresh_parameter_list()
    add_parameter_button.Click += _add_parameters
    remove_parameter_button.Click += _remove_parameters
    move_parameter_up_button.Click += _move_up
    move_parameter_down_button.Click += _move_down
    browse_excel.Click += _browse_excel
    ok_button.Click += _ok
    cancel_button.Click += _cancel
    if load_set_button is not None:
        load_set_button.Click += _load_set
    if save_set_button is not None:
        save_set_button.Click += _save_set
    if delete_set_button is not None:
        delete_set_button.Click += _delete_set
    if batch_export_button is not None:
        batch_export_button.Click += _batch_export
    if sheet_name_box is not None:
        sheet_name_box.TextChanged += _sheet_name_changed
    _refresh_saved_set_dropdown()
    if initial_set_name and saved_set_box is not None:
        saved_set_box.Text = initial_set_name
    _refresh_source_list()
    _initialized[0] = True

    if sheet_name_box is not None:
        initial_sheet_name_stripped = (initial_sheet_name or "").strip()
        if initial_sheet_name_stripped:
            _last_auto_sheet_name[0] = _default_sheet_name_for_item(source_list.SelectedItem)
            _sheet_name_autofill[0] = initial_sheet_name_stripped == _last_auto_sheet_name[0]
            _set_sheet_name_text(initial_sheet_name_stripped)
        else:
            _refresh_sheet_name_default(force=True)

    if not window.ShowDialog():
        if _batch_result[0] is not None:
            selected, paths, sheet_names = _batch_result[0]
            return {"batch_sets": selected, "batch_paths": paths, "batch_sheet_names": sheet_names}
        return None

    selected_item = source_list.SelectedItem
    category_id = _resolve_category_id(selected_item)
    source_id = None
    source_name = ""
    if selected_item is not None:
        if _current_mode() == MODE_BY_CATEGORY:
            source_id = selected_item.id_value
            source_name = selected_item.record["name"]
        else:
            source_id = selected_item.id_value
            source_name = selected_item.view.Name
    units_mode = "project"
    try:
        if units_box is not None:
            idx = units_box.SelectedIndex
            if idx == 1:
                units_mode = "imperial"
            elif idx == 2:
                units_mode = "metric"
    except Exception:
        pass
    return {
        "mode": _current_mode(),
        "source_id": source_id,
        "source_name": source_name,
        "category_id": category_id,
        "selected_param_names": list(selected_params_by_category.get(category_id, [])),
        "excel_path": excel_path.Text or "",
        "sheet_name": (sheet_name_box.Text or "").strip() if sheet_name_box is not None else "",
        "units_mode": units_mode,
    }


def get_section_data(schedule, section_type):
    try:
        return schedule.GetTableData().GetSectionData(section_type)
    except Exception:
        return None


def get_cell_text(schedule, section_type, row, col):
    try:
        return schedule.GetCellText(section_type, row, col) or ""
    except Exception:
        pass
    try:
        section = get_section_data(schedule, section_type)
        if section is not None:
            return section.GetCellText(row, col) or ""
    except Exception:
        pass
    return ""


def get_body_row_element_ids(schedule):
    section = get_section_data(schedule, DB.SectionType.Body)
    if section is None:
        return []
    ids = []
    row_count = int(getattr(section, "NumberOfRows", 0))
    col_count = int(getattr(section, "NumberOfColumns", 0))
    for row in range(row_count):
        found = -1
        for col in range(col_count):
            try:
                elem_id = section.GetCellElementId(row, col)
            except Exception:
                elem_id = None
            found = element_id_value(elem_id)
            if found != -1:
                break
        ids.append(found)
    return ids


def collect_schedule_elements(schedule):
    try:
        elements = list(
            DB.FilteredElementCollector(schedule.Document, schedule.Id)
            .WhereElementIsNotElementType()
            .ToElements()
        )
    except Exception:
        elements = []
    return [elem for elem in elements if elem is not None]


def get_visible_schedule_fields(schedule):
    fields = []
    try:
        definition = schedule.Definition
        field_ids = list(definition.GetFieldOrder())
    except Exception:
        return fields
    for col_index, field_id in enumerate(field_ids):
        try:
            field = definition.GetField(field_id)
        except Exception:
            continue
        if field is None:
            continue
        try:
            if field.IsHidden:
                continue
        except Exception:
            pass
        try:
            heading = (field.ColumnHeading or "").strip()
        except Exception:
            heading = ""
        if not heading:
            try:
                heading = (field.GetName() or "").strip()
            except Exception:
                heading = ""
        fields.append(
            {
                "field": field,
                "column_index": len(fields),
                "heading": heading or "Column {}".format(col_index + 1),
                "param_id": safe_field_parameter_id(field),
            }
        )
    return fields


def safe_field_parameter_id(field):
    try:
        param_id = field.ParameterId
    except Exception:
        param_id = None
    if param_id is not None and element_id_value(param_id) == -1:
        return None
    return param_id


def is_key_schedule(view):
    try:
        definition = view.Definition
    except Exception:
        definition = None
    try:
        return bool(definition and definition.IsKeySchedule)
    except Exception:
        return False


def get_element_type(doc, element):
    try:
        type_id = element.GetTypeId()
    except Exception:
        type_id = None
    if type_id is None or element_id_value(type_id) == -1:
        return None
    try:
        return doc.GetElement(type_id)
    except Exception:
        return None


def get_parameter_from_element_or_type(doc, element, param_id):
    if element is None or param_id is None:
        return None
    try:
        param = element.get_Parameter(param_id)
    except Exception:
        param = None
    if param:
        return param
    elem_type = get_element_type(doc, element)
    if elem_type is None:
        return None
    try:
        return elem_type.get_Parameter(param_id)
    except Exception:
        return None


def parameter_to_text(doc, param):
    if not param:
        return ""
    try:
        value = param.AsValueString()
        if value not in (None, ""):
            return value
    except Exception:
        pass
    try:
        storage = param.StorageType
    except Exception:
        storage = None
    try:
        if storage == DB.StorageType.String:
            return param.AsString() or ""
        if storage == DB.StorageType.Integer:
            return str(param.AsInteger())
        if storage == DB.StorageType.Double:
            return str(param.AsDouble())
        if storage == DB.StorageType.ElementId:
            ref_id = param.AsElementId()
            ref_val = element_id_value(ref_id)
            if ref_val == -1:
                return ""
            ref_elem = doc.GetElement(ref_id)
            if ref_elem is not None:
                for attr in ("Name",):
                    try:
                        value = getattr(ref_elem, attr)
                        if value:
                            return str(value)
                    except Exception:
                        pass
            return str(ref_val)
    except Exception:
        pass
    return ""


def get_parameter_by_name(doc, element, param_name):
    if element is None or not param_name:
        return None
    try:
        param = element.LookupParameter(param_name)
    except Exception:
        param = None
    if param:
        return param
    elem_type = get_element_type(doc, element)
    if elem_type is not None:
        try:
            param = elem_type.LookupParameter(param_name)
        except Exception:
            param = None
        if param:
            return param
    try:
        proj_info = doc.ProjectInformation
        if proj_info is not None:
            return proj_info.LookupParameter(param_name)
    except Exception:
        pass
    return None


def _pick_unit_for_spec(spec_type_id, prefer_imperial):
    try:
        valid = list(DB.UnitUtils.GetValidUnits(spec_type_id))
        if not valid:
            return None
        def _uid_str(uid):
            try:
                return uid.TypeId.lower()
            except Exception:
                return str(uid).lower()
        if prefer_imperial:
            keywords = ["feet", "foot", "inch", "mile", "acre"]
            for uid in valid:
                if any(k in _uid_str(uid) for k in keywords):
                    return uid
        else:
            seeks = ["meters", "metre"]
            skip = ["milli", "centi", "kilo", "micro"]
            for uid in valid:
                s = _uid_str(uid)
                if any(k in s for k in seeks) and not any(k in s for k in skip):
                    return uid
            for uid in valid:
                if "milli" in _uid_str(uid):
                    return uid
        return None
    except Exception:
        return None


def parameter_to_export_value(doc, param, units_mode="project"):
    if not param:
        return ""
    try:
        storage = param.StorageType
    except Exception:
        storage = None
    try:
        if storage == DB.StorageType.String:
            return param.AsString() or ""
        if storage == DB.StorageType.Double:
            raw = param.AsDouble()
            if units_mode == "project":
                # param.GetUnitTypeId() returns the project's display unit for this param
                try:
                    unit_type_id = param.GetUnitTypeId()
                    tid = ""
                    try:
                        tid = unit_type_id.TypeId or ""
                    except Exception:
                        pass
                    if tid:
                        return DB.UnitUtils.ConvertFromInternalUnits(raw, unit_type_id)
                except Exception:
                    pass
            elif units_mode in ("imperial", "metric"):
                spec_type_id = None
                try:
                    spec_type_id = param.Definition.GetSpecTypeId()
                except Exception:
                    pass
                if spec_type_id is not None:
                    try:
                        if DB.UnitUtils.IsMeasurableSpec(spec_type_id):
                            display_unit = _pick_unit_for_spec(spec_type_id, prefer_imperial=(units_mode == "imperial"))
                            if display_unit is not None:
                                return DB.UnitUtils.ConvertFromInternalUnits(raw, display_unit)
                    except Exception:
                        pass
            return raw
        if storage == DB.StorageType.Integer:
            value_string = param.AsValueString()
            if value_string not in (None, "") and value_string not in ("0", "1"):
                return value_string
            return param.AsInteger()
        if storage == DB.StorageType.ElementId:
            ref_id = param.AsElementId()
            ref_value = element_id_value(ref_id)
            if ref_value == -1:
                return ""
            ref_elem = doc.GetElement(ref_id)
            if ref_elem is not None:
                try:
                    name = getattr(ref_elem, "Name", None)
                    if name:
                        return name
                except Exception:
                    pass
            return str(ref_value)
    except Exception:
        pass
    try:
        value = param.AsValueString()
        if value not in (None, ""):
            return value
    except Exception:
        pass
    return ""


def build_category_export_rows(doc, category_id, param_names, units_mode="project"):
    elements = get_elements_by_category(doc, category_id)
    elements.sort(key=lambda item: element_id_value(item.Id))
    headers = ["Id"] + list(param_names or [])
    rows = []
    for element in elements:
        row = [element_id_value(element.Id)]
        for param_name in param_names or []:
            row.append(parameter_to_export_value(doc, get_parameter_by_name(doc, element, param_name), units_mode=units_mode))
        rows.append(row)
    return headers, rows, len(elements)


def build_schedule_body_rows(schedule):
    section = get_section_data(schedule, DB.SectionType.Body)
    if section is None:
        return []
    row_count = int(getattr(section, "NumberOfRows", 0))
    col_count = int(getattr(section, "NumberOfColumns", 0))
    rows = []
    for row in range(row_count):
        rows.append([get_cell_text(schedule, DB.SectionType.Body, row, col) for col in range(col_count)])
    return rows


def build_schedule_row_lookup(schedule):
    row_ids = get_body_row_element_ids(schedule)
    row_values = build_schedule_body_rows(schedule)
    lookup = {}
    for row_id, values in zip(row_ids, row_values):
        if row_id == -1:
            continue
        lookup.setdefault(row_id, []).append(values)
    return row_ids, lookup


def build_table_headers(schedule, column_count):
    section = get_section_data(schedule, DB.SectionType.Header)
    row_count = int(getattr(section, "NumberOfRows", 0)) if section is not None else 0
    if row_count > 0:
        last_row = row_count - 1
        headers = [get_cell_text(schedule, DB.SectionType.Header, last_row, col) for col in range(column_count)]
        if any(header.strip() for header in headers):
            return [header or "Column {}".format(idx + 1) for idx, header in enumerate(headers)]
    return ["Column {}".format(idx + 1) for idx in range(column_count)]


def order_schedule_elements(schedule, elements, row_ids):
    elem_by_id = {}
    ordered = []
    seen = set()
    for elem in elements:
        elem_by_id[element_id_value(elem.Id)] = elem
    for row_id in row_ids:
        if row_id in seen:
            continue
        elem = elem_by_id.get(row_id)
        if elem is not None:
            ordered.append(elem)
            seen.add(row_id)
    for elem in sorted(elements, key=lambda item: element_id_value(item.Id)):
        elem_id = element_id_value(elem.Id)
        if elem_id not in seen:
            ordered.append(elem)
            seen.add(elem_id)
    return ordered


def get_field_text_for_element(doc, schedule, element, field_info, row_lookup):
    param_id = field_info.get("param_id")
    value = ""
    if param_id is not None:
        value = parameter_to_text(doc, get_parameter_from_element_or_type(doc, element, param_id))
    if value not in (None, ""):
        return value
    row_queue = row_lookup.get(element_id_value(element.Id)) or []
    if row_queue:
        column_index = int(field_info.get("column_index", 0))
        if column_index < len(row_queue[0]):
            return row_queue[0][column_index]
    return ""


def _inject_element_ids(headers, rows, ids):
    """Prepend an 'Element ID' column to headers and rows.

    `ids` is a parallel list of integer element IDs (or -1 for rows without
    a traceable element, e.g. group-header rows).  Rows with id == -1 get an
    empty cell in the Element ID column.
    """
    new_headers = ["Element ID"] + list(headers)
    new_rows = []
    for i, row in enumerate(rows):
        eid = ids[i] if i < len(ids) else -1
        new_rows.append(([str(eid)] if eid != -1 else [""]) + list(row))
    return new_headers, new_rows


def strip_leading_header_and_blanks(body_rows, headers=None):
    """Remove duplicate column-header rows and blank separator rows from the
    top of the body data.

    Some schedule types (key schedules, schedules with the 'blank row before
    data' appearance option) include the column-header row and/or a blank row
    as the first rows of the body section.  Because the exporter already writes
    its own bold header row, those body rows must be dropped to avoid
    duplication and empty rows in the output.
    """
    if not body_rows:
        return body_rows
    result = list(body_rows)
    headers_lower = [h.strip().lower() for h in headers] if headers else []
    while result:
        row = result[0]
        row_lower = [cell.strip().lower() for cell in row]
        if headers_lower and row_lower == headers_lower:
            result.pop(0)
        elif all(cell == "" for cell in row):
            result.pop(0)
        else:
            break
    return result


def _strip_body_rows(body_rows, headers, raw_ids):
    """Strip leading header/blank rows and keep raw_ids in sync.

    Returns (stripped_rows, aligned_ids) where aligned_ids[i] is the element
    ID that corresponds to stripped_rows[i].
    """
    n_before = len(body_rows)
    stripped = strip_leading_header_and_blanks(body_rows, headers)
    n_stripped = n_before - len(stripped)
    aligned_ids = (raw_ids[n_stripped:n_stripped + len(stripped)]
                   if raw_ids else [-1] * len(stripped))
    return stripped, aligned_ids


def build_schedule_export_rows(doc, schedule):
    fields = get_visible_schedule_fields(schedule)
    elements = collect_schedule_elements(schedule)
    key_sched = is_key_schedule(schedule)

    # GetCellText-based approach is the most reliable primary source because it
    # reflects what Revit actually renders in the schedule, covering calculated
    # value fields and schedules where GetCellElementId returns invalid IDs
    # (grouped schedules, key schedules, etc.).
    body_rows = build_schedule_body_rows(schedule)
    # Fetch element IDs now, before any stripping, so indices stay aligned.
    raw_ids = [] if key_sched else get_body_row_element_ids(schedule)

    if not fields:
        headers = build_table_headers(schedule, len(body_rows[0]) if body_rows else 0)
        body_rows, aligned_ids = _strip_body_rows(body_rows, headers, raw_ids)
        if not key_sched:
            headers, body_rows = _inject_element_ids(headers, body_rows, aligned_ids)
        return headers, body_rows, len(elements)

    headers = [field["heading"] for field in fields]

    # Use direct cell text when available and column count matches visible fields.
    if body_rows and len(body_rows[0]) == len(fields):
        body_rows, aligned_ids = _strip_body_rows(body_rows, headers, raw_ids)
        if not key_sched:
            headers, body_rows = _inject_element_ids(headers, body_rows, aligned_ids)
        return headers, body_rows, len(elements)

    # Fall back to element-based parameter lookup when GetCellText gives nothing
    # or the column count doesn't match.
    row_ids, row_lookup = build_schedule_row_lookup(schedule)
    ordered_elements = order_schedule_elements(schedule, elements, row_ids)
    if not ordered_elements:
        body_rows, aligned_ids = _strip_body_rows(body_rows, headers, raw_ids)
        if not key_sched:
            headers, body_rows = _inject_element_ids(headers, body_rows, aligned_ids)
        return headers, body_rows, len(elements)

    rows = []
    for element in ordered_elements:
        row = [get_field_text_for_element(doc, schedule, element, field, row_lookup) for field in fields]
        rows.append(row)

    # If the element-based approach also produces all-empty data, prefer the
    # raw cell-text rows even when the column count differs.
    if rows and all(all(cell == "" for cell in row) for row in rows) and body_rows:
        body_rows, aligned_ids = _strip_body_rows(body_rows, headers, raw_ids)
        if not key_sched:
            headers, body_rows = _inject_element_ids(headers, body_rows, aligned_ids)
        return headers, body_rows, len(elements)

    # Element-based path: IDs come from the ordered elements directly.
    if not key_sched:
        elem_ids = [element_id_value(e.Id) for e in ordered_elements]
        headers, rows = _inject_element_ids(headers, rows, elem_ids)
    return headers, rows, len(elements)


def auto_fit_columns(sheet):
    for column_cells in sheet.columns:
        max_length = 0
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            if len(value) > max_length:
                max_length = len(value)
        column_letter = column_cells[0].column_letter
        sheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 60)


def make_unique_name(base, used):
    candidate = base
    if candidate not in used:
        used.add(candidate)
        return candidate
    idx = 1
    while True:
        suffix = "_{}".format(idx)
        trimmed = candidate[: 31 - len(suffix)]
        attempt = "{}{}".format(trimmed, suffix)
        if attempt not in used:
            used.add(attempt)
            return attempt
        idx += 1


def export_to_excel(doc, category_name, category_id, param_names, file_path, ui, sheet_name=None, units_mode="project"):
    add_lib_path()
    try:
        import WWP_xlsx as openpyxl
        from WWP_xlsx import Font
    except Exception as exc:
        ui.uiUtils_alert("Excel writer is not available.\n{}".format(exc), title="Export2Ex Beta")
        return False

    if os.path.exists(file_path):
        load_kwargs = {}
        if os.path.splitext(file_path)[1].lower() == ".xlsm":
            load_kwargs["keep_vba"] = True
        workbook = openpyxl.load_workbook(file_path, **load_kwargs)
    else:
        workbook = openpyxl.Workbook()

    base_name = sanitize_sheet_name(sheet_name if sheet_name else _pluralize(category_name))
    sheet_name = base_name
    if sheet_name in workbook.sheetnames:
        existing = workbook[sheet_name]
        sheet_index = workbook.worksheets.index(existing)
        workbook.remove(existing)
        sheet = workbook.create_sheet(title=sheet_name, index=sheet_index)
    else:
        sheet = workbook.create_sheet(title=sheet_name)

    headers, rows, elem_count = build_category_export_rows(doc, category_id, param_names, units_mode=units_mode)
    log_message(
        "Category '{}' resolved {} elements and {} export columns".format(
            category_name, elem_count, len(headers)
        )
    )

    for col_index, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=col_index, value=header)
        cell.font = Font(bold=True)
    for row_index, row in enumerate(rows, start=2):
        for col_index, value in enumerate(row, start=1):
            sheet.cell(row=row_index, column=col_index, value=value)

    if headers:
        sheet.freeze_panes = "A2"
    auto_fit_columns(sheet)

    if "Sheet" in workbook.sheetnames and len(workbook.sheetnames) > 1:
        workbook.remove(workbook["Sheet"])

    workbook.save(file_path)
    return True


def show_error_report(ui, exc):
    report = "Export2Ex Beta failed.\n\n{}\n\nLog File\n{}".format(str(exc), _log_file_path())
    try:
        ui.uiUtils_alert(report, title="Export2Ex Beta")
    except Exception:
        pass


def main():
    log_message("main start")
    doc = get_active_doc()
    ui = load_uiutils()
    if doc is None:
        ui.uiUtils_alert("No active Revit document found.", title="Export2Ex Beta")
        return

    schedules = [ScheduleItem(view) for view in collect_schedules(doc)]
    categories = [CategoryItem(record) for record in collect_category_records(doc)]

    config, save_config = get_config_and_saver()
    default_dir = get_default_dir(doc)
    last_excel_path = config_get(config, CONFIG_LAST_EXCEL_PATH, "")
    init_excel_path = last_excel_path or os.path.join(default_dir, "CategoryExport.xlsx")
    last_mode = _normalize_mode(config_get(config, CONFIG_LAST_MODE, MODE_BY_CATEGORY))
    last_source_id = config_get(
        config,
        CONFIG_LAST_CATEGORY_ID if last_mode == MODE_BY_CATEGORY else CONFIG_LAST_SCHEDULE_ID,
        None,
    )
    last_category_id = _coerce_int(config_get(config, CONFIG_LAST_CATEGORY_ID, None), None)
    last_param_names = _coerce_string_list(config_get(config, CONFIG_LAST_PARAM_NAMES, []))

    def _handle_single_export(result):
        if not result:
            return
        category_id_value = _coerce_int(result.get("category_id"), None)
        if category_id_value in (None, -1):
            ui.uiUtils_alert("Select a schedule or category with a valid category.", title="Export2Ex Beta")
            return
        selected_param_names = result.get("selected_param_names") or []
        if not selected_param_names:
            ui.uiUtils_alert("Select at least one parameter to export.", title="Export2Ex Beta")
            return
        file_path = normalize_excel_output_path(result.get("excel_path"))
        if not file_path:
            ui.uiUtils_alert("Choose an Excel file path ending with .xlsx or .xlsm.", title="Export2Ex Beta")
            return
        category_record = None
        for item in categories:
            if item.id_value == category_id_value:
                category_record = item.record
                break
        category_name = category_record["name"] if category_record else (result.get("source_name") or "Category Export")
        sheet_name_raw = (result.get("sheet_name") or "").strip()
        if not export_to_excel(doc, category_name, DB.ElementId(category_id_value), selected_param_names, file_path, ui, sheet_name=sheet_name_raw, units_mode=result.get("units_mode", "project")):
            return
        config.last_mode = _normalize_mode(result.get("mode"))
        config.last_schedule_id = result.get("source_id") if config.last_mode == MODE_FROM_SCHEDULE else None
        config.last_category_id = category_id_value
        config.last_param_names = list(selected_param_names)
        config.last_excel_path = file_path
        config.last_sheet_name = sheet_name_raw
        save_config()
        try:
            os.startfile(file_path)
        except Exception:
            pass
        ui.uiUtils_alert("Export complete.", title="Export2Ex Beta")

    def _open_form(set_name="", set_mode=None, set_source_id=None, set_category_id=None,
                   set_param_names=None, set_sheet_name="", set_excel_path=""):
        form_mode = _normalize_mode(set_mode) if set_mode else last_mode
        form_source_id = set_source_id if set_source_id is not None else last_source_id
        form_cat_id = _coerce_int(set_category_id, None) if set_category_id is not None else last_category_id
        form_params = _coerce_string_list(set_param_names) if set_param_names else last_param_names
        form_excel = set_excel_path or init_excel_path
        result = show_export_form(
            ui, doc, schedules, categories,
            form_excel, form_mode, form_source_id, form_cat_id, form_params,
            set_sheet_name, set_name,
        )
        if result and "batch_sets" not in result:
            _handle_single_export(result)

    def _add_callback():
        _open_form()

    def _edit_callback(sname, sdata):
        _open_form(
            set_name=sname,
            set_mode=sdata.get("mode"),
            set_source_id=sdata.get("source_id"),
            set_category_id=sdata.get("category_id"),
            set_param_names=sdata.get("param_names"),
            set_sheet_name=(sdata.get("sheet_name") or "").strip(),
            set_excel_path=(sdata.get("excel_path") or "").strip(),
        )

    saved_sets = read_saved_sets(doc)
    batch_result = _show_beta_batch_dialog(
        saved_sets, doc, ui=ui, add_callback=_add_callback, edit_callback=_edit_callback
    )

    if batch_result is None:
        return

    selected, batch_paths, batch_sheet_names = batch_result
    if not selected:
        ui.uiUtils_alert("No sets selected for batch export.", title="Export2Ex Beta")
        return

    exported = []
    failed = []
    for sname in selected:
        sdata = saved_sets.get(sname)
        if not isinstance(sdata, dict):
            failed.append("{}: set not found".format(sname))
            continue
        cat_id_val = _coerce_int(sdata.get("category_id"), None)
        if cat_id_val in (None, -1):
            failed.append("{}: no category".format(sname))
            continue
        param_names = sdata.get("param_names") or []
        if not param_names:
            failed.append("{}: no parameters configured".format(sname))
            continue
        file_path = normalize_excel_output_path(batch_paths.get(sname) or "")
        if not file_path:
            failed.append("{}: no output file path".format(sname))
            continue
        sheet_name_val = (batch_sheet_names.get(sname) or "").strip()
        category_record = None
        for item in categories:
            if item.id_value == cat_id_val:
                category_record = item.record
                break
        category_name = category_record["name"] if category_record else sname
        try:
            ok = export_to_excel(
                doc, category_name, DB.ElementId(cat_id_val),
                param_names, file_path, ui, sheet_name=sheet_name_val,
            )
            if ok:
                exported.append(sname)
            else:
                failed.append("{}: export failed".format(sname))
        except Exception as exc:
            log_exception("batch export {}".format(sname), exc)
            failed.append("{}: {}".format(sname, str(exc)))

    changed = False
    for sname, new_path in batch_paths.items():
        sdata = saved_sets.get(sname)
        if not isinstance(sdata, dict):
            continue
        if new_path and new_path != (sdata.get("excel_path") or "").strip():
            sdata["excel_path"] = new_path
            changed = True
    for sname, new_sheet in batch_sheet_names.items():
        sdata = saved_sets.get(sname)
        if not isinstance(sdata, dict):
            continue
        if new_sheet and new_sheet != (sdata.get("sheet_name") or "").strip():
            sdata["sheet_name"] = new_sheet
            changed = True
    if changed:
        write_saved_sets(doc, saved_sets)

    msg_parts = []
    if exported:
        msg_parts.append("Exported: {}".format(", ".join(exported)))
    if failed:
        msg_parts.append("Failed:\n{}".format("\n".join(failed)))
    ui.uiUtils_alert(
        "\n\n".join(msg_parts) if msg_parts else "Nothing exported.",
        title="Export2Ex Beta",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log_exception("Unhandled exception in Export2Ex Beta", exc)
        try:
            show_error_report(load_uiutils(), exc)
        except Exception:
            pass
        raise
