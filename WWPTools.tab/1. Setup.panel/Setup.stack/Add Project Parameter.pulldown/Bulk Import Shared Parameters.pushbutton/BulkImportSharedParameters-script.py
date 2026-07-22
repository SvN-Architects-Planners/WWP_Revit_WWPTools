import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Data')
clr.AddReference('PresentationFramework')

import os
import posixpath
import re
import traceback
import zipfile
import xml.etree.ElementTree as ET

import Autodesk.Revit.DB as DB
import System
import System.Data as SD
from Autodesk.Revit.UI import TaskDialog
from System import Enum
from System.Collections.Generic import List as NetList
from System.Windows.Controls import DataGridEditingUnit
from pyrevit import forms

import WWP_uiUtils as ui


doc = __revit__.ActiveUIDocument.Document
app = doc.Application

DEFAULT_SHARED_PARAMETERS_PATH = (
    r"N:\Library\Design Software\Autodesk\Revit\Shared Parameters\SharedParameters.txt"
)
DEFAULT_EXCEL_SHEET_NAME = "Project Parameters"


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _safe_str(value):
    if value is None or value == System.DBNull.Value:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def _net_string_list(values):
    result = NetList[System.String]()
    for value in values:
        result.Add(value)
    return result


def _id_value(element_id):
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def _humanize_api_name(name):
    name = str(name or "")
    name = name.replace("PG_", "").replace("_", " ")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return " ".join(word.capitalize() for word in name.split())


def _unique_label(base_label, qualifier, used_labels):
    base_label = _safe_str(base_label) or _safe_str(qualifier) or "Unnamed"
    label = base_label
    if label.lower() in used_labels:
        label = "{} [{}]".format(base_label, qualifier)
    candidate = label
    suffix = 2
    while candidate.lower() in used_labels:
        candidate = "{} ({})".format(label, suffix)
        suffix += 1
    used_labels.add(candidate.lower())
    return candidate


def _choose_shared_parameter_file(current_path=""):
    initial_dir = ""
    try:
        initial_dir = os.path.dirname(current_path or "")
    except Exception:
        pass
    return ui.uiUtils_open_file_dialog(
        title="Select Shared Parameters File",
        filter_text="Shared Parameters (*.txt)|*.txt|All Files (*.*)|*.*",
        multiselect=False,
        initial_directory=initial_dir if os.path.isdir(initial_dir) else "",
    )


def _choose_excel_file(current_path=""):
    initial_dir = ""
    try:
        initial_dir = os.path.dirname(current_path or "")
    except Exception:
        pass
    return ui.uiUtils_open_file_dialog(
        title="Import Parameter Rows from Excel",
        filter_text="Excel Workbooks (*.xlsx;*.xlsm)|*.xlsx;*.xlsm|All Files (*.*)|*.*",
        multiselect=False,
        initial_directory=initial_dir if os.path.isdir(initial_dir) else "",
    )


# ---------------------------------------------------------------------------
# Excel row reader (Open XML; Microsoft Excel is not required)
# ---------------------------------------------------------------------------

def _xml_children(element, local_name):
    if element is None:
        return []
    return [child for child in element.iter() if child.tag.split("}")[-1] == local_name]


def _excel_column_index(cell_reference):
    letters = "".join(char for char in _safe_str(cell_reference) if char.isalpha())
    result = 0
    for char in letters.upper():
        result = (result * 26) + (ord(char) - ord("A") + 1)
    return result - 1


def _excel_cell_text(cell, shared_strings):
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in _xml_children(cell, "t"))

    values = _xml_children(cell, "v")
    raw = values[0].text if values and values[0].text is not None else ""
    if cell_type == "s" and raw:
        try:
            return shared_strings[int(raw)]
        except Exception:
            return raw
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def _workbook_part_path(target):
    target = _safe_str(target).replace("\\", "/")
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return posixpath.normpath(posixpath.join("xl", target))


def _read_excel_rows(file_path, preferred_sheet=DEFAULT_EXCEL_SHEET_NAME):
    if not file_path or not os.path.isfile(file_path):
        raise Exception("Excel workbook not found:\n{}".format(file_path or "(none)"))

    with zipfile.ZipFile(file_path, "r") as workbook:
        names = set(workbook.namelist())
        shared_strings = []
        if "xl/sharedStrings.xml" in names:
            shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            for shared_item in _xml_children(shared_root, "si"):
                shared_strings.append(
                    "".join(node.text or "" for node in _xml_children(shared_item, "t"))
                )

        workbook_root = ET.fromstring(workbook.read("xl/workbook.xml"))
        relationships_root = ET.fromstring(
            workbook.read("xl/_rels/workbook.xml.rels")
        )
        relationships = {}
        for relationship in _xml_children(relationships_root, "Relationship"):
            relationships[relationship.attrib.get("Id", "")] = relationship.attrib.get(
                "Target", ""
            )

        relationship_attribute = (
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        sheets = []
        for sheet in _xml_children(workbook_root, "sheet"):
            name = sheet.attrib.get("name", "")
            relationship_id = sheet.attrib.get(relationship_attribute, "")
            target = relationships.get(relationship_id, "")
            if name and target:
                sheets.append((name, _workbook_part_path(target)))

        if not sheets:
            raise Exception("The workbook contains no readable worksheets.")

        selected_sheet = None
        for sheet_name, part_path in sheets:
            if sheet_name.lower() == preferred_sheet.lower():
                selected_sheet = (sheet_name, part_path)
                break
        if selected_sheet is None:
            selected_sheet = sheets[0]

        sheet_name, part_path = selected_sheet
        if part_path not in names:
            raise Exception("Worksheet data could not be found for '{}'.".format(sheet_name))
        sheet_root = ET.fromstring(workbook.read(part_path))

        rows = []
        for row_node in _xml_children(sheet_root, "row"):
            values_by_column = {}
            max_column = -1
            for cell in [node for node in row_node if node.tag.split("}")[-1] == "c"]:
                column = _excel_column_index(cell.attrib.get("r", ""))
                if column < 0:
                    continue
                values_by_column[column] = _excel_cell_text(cell, shared_strings)
                max_column = max(max_column, column)
            if max_column >= 0:
                rows.append([values_by_column.get(index, "") for index in range(max_column + 1)])
            else:
                rows.append([])

    return sheet_name, rows


def _normalized_excel_header(value):
    return "".join(char for char in _safe_str(value).lower() if char.isalnum())


def _excel_column(header_row, aliases):
    normalized = [_normalized_excel_header(value) for value in header_row]
    for alias in aliases:
        key = _normalized_excel_header(alias)
        if key in normalized:
            return normalized.index(key)
    return None


def _excel_value(row, column):
    if column is None or column < 0 or column >= len(row):
        return ""
    return _safe_str(row[column])


def _excel_binding_value(raw_value):
    normalized = _safe_str(raw_value).lower()
    if normalized in ("1", "true", "yes", "y", "instance", "instance parameter"):
        return "Instance"
    if normalized in ("0", "false", "no", "n", "type", "type parameter"):
        return "Type"
    return ""


def _extract_excel_entries(rows):
    aliases = {
        "parameter": ("Parameter Name", "Shared Parameter", "Parameter", "Name"),
        "shared_group": ("Group Name", "Shared Parameter Group", "Shared Group"),
        "binding": ("Instance Parameter", "Instance / Type", "Binding", "Binding Type"),
        "group": ("Parameter Group", "Revit Parameter Group", "Revit Group", "Group"),
        "category": ("Category", "Revit Category", "Category Name"),
        "category_api": ("Category API", "Built In Category", "BuiltInCategory"),
    }

    best = None
    for row_index, candidate in enumerate(rows[:20]):
        columns = dict(
            (field, _excel_column(candidate, field_aliases))
            for field, field_aliases in aliases.items()
        )
        score = sum(1 for value in columns.values() if value is not None)
        if best is None or score > best[0]:
            best = (score, row_index, columns)

    if best is None or best[0] == 0:
        raise Exception(
            "No recognized headers were found. Include at least a Parameter Name, "
            "Instance/Type, Group, or Category column."
        )

    header_index = best[1]
    columns = best[2]
    entries = []
    for source_row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        category_name = _excel_value(row, columns["category"])
        category_api = _excel_value(row, columns["category_api"])
        entry = {
            "source_row": source_row_number,
            "parameter": _excel_value(row, columns["parameter"]),
            "shared_group": _excel_value(row, columns["shared_group"]),
            "binding_raw": _excel_value(row, columns["binding"]),
            "group": _excel_value(row, columns["group"]),
            "category": category_name or category_api,
        }
        if any(entry[field] for field in ("parameter", "shared_group", "binding_raw", "group", "category")):
            entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Shared parameter, group, and category choices
# ---------------------------------------------------------------------------

def _open_shared_parameter_file(file_path):
    if not file_path or not os.path.isfile(file_path):
        raise Exception("Shared Parameters file not found:\n{}".format(file_path or "(none)"))
    app.SharedParametersFilename = file_path
    definition_file = app.OpenSharedParameterFile()
    if definition_file is None:
        raise Exception("Revit could not open the selected Shared Parameters file.")
    return definition_file


def _shared_parameter_choices(definition_file):
    records = []
    name_counts = {}
    for definition_group in definition_file.Groups:
        group_name = _safe_str(definition_group.Name) or "Ungrouped"
        for definition in definition_group.Definitions:
            name = _safe_str(definition.Name)
            if not name:
                continue
            records.append((name, group_name, definition))
            key = name.lower()
            name_counts[key] = name_counts.get(key, 0) + 1

    records.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    labels = []
    lookup = {}
    used = set()
    for name, group_name, definition in records:
        base = name
        if name_counts.get(name.lower(), 0) > 1:
            base = "{} [{}]".format(name, group_name)
        label = _unique_label(base, group_name, used)
        labels.append(label)
        lookup[label] = definition
    return labels, lookup


def _forge_parameter_groups():
    records = []
    try:
        for group_id in DB.ParameterUtils.GetAllBuiltInGroups():
            type_id = _safe_str(group_id.TypeId)
            api_name = type_id.split(":")[-1].split("-")[0] if type_id else "Group"
            try:
                display = _safe_str(DB.LabelUtils.GetLabelForGroup(group_id))
            except Exception:
                display = ""
            records.append((display or _humanize_api_name(api_name), api_name, group_id))
    except Exception:
        records = []
    if records:
        return records

    # Static .NET properties are not always returned by dir() under every
    # pyRevit engine. Keep the known API property names as a fallback.
    known_names = (
        "AdskModelProperties", "AnalysisResults", "AnalyticalAlignment",
        "Constraints", "Construction", "Data", "Dimensions",
        "DivisionGeometry", "Electrical", "ElectricalCircuiting",
        "ElectricalEngineering", "ElectricalLighting", "ElectricalLoads",
        "EnergyAnalysis", "FireProtection", "General", "Geometry",
        "GreenBuilding", "IdentityData", "Ifc", "Layers", "LifeSafety",
        "Materials", "Mechanical", "MechanicalAirflow", "MechanicalLoads",
        "ModelProperties", "Moments", "Other", "OverallLegend", "Phasing",
        "Photometrics", "Plumbing", "PrimaryEnd", "RebarArray",
        "RebarSystemLayers", "Reference", "SecondaryEnd", "SegmentsFittings",
        "SlabShapeEdit", "Structural", "StructuralAnalysis", "Text", "Title",
        "Visibility",
    )
    api_names = set(dir(DB.GroupTypeId))
    api_names.update(known_names)
    for api_name in api_names:
        if api_name.startswith("_") or api_name.lower() in ("invalid", "empty"):
            continue
        try:
            group_id = getattr(DB.GroupTypeId, api_name)
            type_id = _safe_str(group_id.TypeId)
        except Exception:
            continue
        if not type_id or "parameter.group" not in type_id.lower():
            continue
        try:
            display = _safe_str(DB.LabelUtils.GetLabelForGroup(group_id))
        except Exception:
            display = ""
        records.append((display or _humanize_api_name(api_name), api_name, group_id))
    return records


def _legacy_parameter_groups():
    records = []
    for group_enum in Enum.GetValues(DB.BuiltInParameterGroup):
        api_name = _safe_str(group_enum)
        if api_name.upper() == "INVALID":
            continue
        try:
            display = _safe_str(DB.LabelUtils.GetLabelFor(group_enum))
        except Exception:
            display = ""
        records.append((display or _humanize_api_name(api_name), api_name, group_enum))
    return records


def _parameter_group_choices():
    try:
        revit_year = int(app.VersionNumber)
    except Exception:
        revit_year = 0

    if revit_year >= 2022 and hasattr(DB, "GroupTypeId"):
        records = _forge_parameter_groups()
    else:
        records = _legacy_parameter_groups()

    records.sort(key=lambda item: (item[0].lower(), item[1].lower()))
    labels = []
    lookup = {}
    used = set()
    default_label = ""
    for display, api_name, group_id in records:
        label = _unique_label(display, api_name, used)
        labels.append(label)
        lookup[label] = group_id
        if api_name.lower() in ("identitydata", "pg_identity_data"):
            default_label = label

    if not default_label:
        for label in labels:
            if "identity" in label.lower():
                default_label = label
                break
    if not default_label and labels:
        default_label = labels[0]
    return labels, lookup, default_label


def _category_choices():
    records = []
    for category in doc.Settings.Categories:
        try:
            if not category.AllowsBoundParameters:
                continue
        except Exception:
            continue
        try:
            name = _safe_str(category.Name)
            category_id = _id_value(category.Id)
        except Exception:
            continue
        if name:
            records.append((name, category_id, category))

    records.sort(key=lambda item: (item[0].lower(), item[1]))
    labels = []
    lookup = {}
    used = set()
    for name, category_id, category in records:
        label = _unique_label(name, category_id, used)
        labels.append(label)
        lookup[label] = category
    return labels, lookup


# ---------------------------------------------------------------------------
# WPF selection grid
# ---------------------------------------------------------------------------

class BulkImportWindow(forms.WPFWindow):

    def __init__(self, shared_parameter_path):
        forms.WPFWindow.__init__(self, "BulkImportSharedParameters.xaml")
        self.result_rows = None
        self.shared_parameter_path = ""
        self._excel_path = ""
        self._parameter_lookup = {}

        self._group_labels, self._group_lookup, self._default_group = _parameter_group_choices()
        self._category_labels, self._category_lookup = _category_choices()
        self._binding_labels = ["Instance", "Type"]

        self._setup_grid()
        self._wire_events()
        self._load_shared_file(shared_parameter_path, clear_rows=True)

    def _setup_grid(self):
        self._table = SD.DataTable("SharedParameterBindings")
        self._table.Columns.Add("Parameter", System.String)
        self._table.Columns.Add("Binding", System.String)
        self._table.Columns.Add("Group", System.String)
        self._table.Columns.Add("Category", System.String)
        self._table.Columns.Add("ImportNotes", System.String)
        self.ParameterGrid.ItemsSource = self._table.DefaultView

        self.ParameterGrid.Columns[1].ItemsSource = _net_string_list(self._binding_labels)
        self.ParameterGrid.Columns[2].ItemsSource = _net_string_list(self._group_labels)
        self.ParameterGrid.Columns[3].ItemsSource = _net_string_list(self._category_labels)

    def _wire_events(self):
        self.BrowseButton.Click += self._browse_click
        self.ExcelImportButton.Click += self._excel_import_click
        self.AddRowButton.Click += self._add_click
        self.DuplicateRowButton.Click += self._duplicate_click
        self.RemoveRowButton.Click += self._remove_click
        self.CancelButton.Click += self._cancel_click
        self.ImportButton.Click += self._import_click

    def _load_shared_file(self, file_path, clear_rows=False):
        definition_file = _open_shared_parameter_file(file_path)
        labels, lookup = _shared_parameter_choices(definition_file)
        if not labels:
            raise Exception("The selected file contains no shared parameter definitions.")

        self.shared_parameter_path = file_path
        self.SharedFileBox.Text = file_path
        self._parameter_lookup = lookup
        self.ParameterGrid.Columns[0].ItemsSource = _net_string_list(labels)
        self.ParameterCountText.Text = "{} shared parameter(s) available".format(len(labels))

        if clear_rows:
            self._table.Clear()
            self._add_row()

    def _add_row(
        self,
        parameter="",
        binding="Instance",
        group="",
        category="",
        import_notes="",
        use_defaults=True,
    ):
        row = self._table.NewRow()
        row["Parameter"] = parameter
        row["Binding"] = (binding or "Instance") if use_defaults else binding
        row["Group"] = (group or self._default_group) if use_defaults else group
        row["Category"] = category
        row["ImportNotes"] = import_notes
        self._table.Rows.Add(row)
        return row

    def _selected_values(self):
        selected = self.ParameterGrid.SelectedItem
        if selected is None:
            return None
        return (
            _safe_str(selected["Parameter"]),
            _safe_str(selected["Binding"]),
            _safe_str(selected["Group"]),
            _safe_str(selected["Category"]),
            _safe_str(selected["ImportNotes"]),
        )

    def _commit_grid(self):
        self.ParameterGrid.CommitEdit(DataGridEditingUnit.Cell, True)
        self.ParameterGrid.CommitEdit(DataGridEditingUnit.Row, True)

    def _browse_click(self, sender, args):
        selected = _choose_shared_parameter_file(self.shared_parameter_path)
        if not selected:
            return
        try:
            self._load_shared_file(selected, clear_rows=True)
            self.StatusText.Text = "Loaded a new Shared Parameters file. Add binding rows below."
        except Exception as exc:
            TaskDialog.Show("Bulk Import Shared Parameters", str(exc))

    def _normalized_option(self, value, prefixes=()):
        normalized = _normalized_excel_header(value)
        for prefix in prefixes:
            normalized_prefix = _normalized_excel_header(prefix)
            if normalized.startswith(normalized_prefix):
                normalized = normalized[len(normalized_prefix):]
                break
        return normalized

    def _match_parameter(self, raw_name, raw_group=""):
        name_key = self._normalized_option(raw_name)
        group_key = self._normalized_option(raw_group)
        if not name_key:
            return ""

        matches = []
        grouped_matches = []
        for label, definition in self._parameter_lookup.items():
            if self._normalized_option(definition.Name) != name_key:
                continue
            matches.append(label)
            if group_key:
                try:
                    owner_group = definition.OwnerGroup.Name
                except Exception:
                    owner_group = ""
                if self._normalized_option(owner_group) == group_key:
                    grouped_matches.append(label)

        if len(grouped_matches) == 1:
            return grouped_matches[0]
        if len(matches) == 1:
            return matches[0]
        return ""

    def _match_group(self, raw_group):
        raw_key = self._normalized_option(raw_group, ("PG_",))
        if not raw_key:
            return ""
        matches = []
        for label, group_id in self._group_lookup.items():
            aliases = [label, _safe_str(group_id)]
            try:
                type_id = _safe_str(group_id.TypeId)
                aliases.append(type_id)
                aliases.append(type_id.split(":")[-1].split("-")[0])
            except Exception:
                pass
            alias_keys = set(self._normalized_option(alias, ("PG_",)) for alias in aliases)
            if raw_key in alias_keys:
                matches.append(label)
        return matches[0] if len(matches) == 1 else ""

    def _match_category(self, raw_category):
        raw_key = self._normalized_option(raw_category, ("OST_",))
        if not raw_key:
            return ""
        matches = []
        for label, category in self._category_lookup.items():
            aliases = [label, _safe_str(category.Name)]
            try:
                aliases.append(_safe_str(category.BuiltInCategory))
            except Exception:
                try:
                    aliases.append(_safe_str(Enum.GetName(DB.BuiltInCategory, _id_value(category.Id))))
                except Exception:
                    pass
            alias_keys = set(self._normalized_option(alias, ("OST_",)) for alias in aliases)
            if raw_key in alias_keys:
                matches.append(label)
        return matches[0] if len(matches) == 1 else ""

    def _has_meaningful_rows(self):
        for row in self._table.Rows:
            if _safe_str(row["Parameter"]) or _safe_str(row["Category"]):
                return True
            if _safe_str(row["Binding"]) not in ("", "Instance"):
                return True
            if _safe_str(row["Group"]) not in ("", self._default_group):
                return True
        return False

    def _excel_import_click(self, sender, args):
        selected = _choose_excel_file(self._excel_path)
        if not selected:
            return

        try:
            sheet_name, excel_rows = _read_excel_rows(selected)
            entries = _extract_excel_entries(excel_rows)
        except Exception as exc:
            TaskDialog.Show(
                "Import Rows from Excel",
                "Rows could not be read from the workbook.\n\n{}".format(exc),
            )
            return

        if not entries:
            TaskDialog.Show(
                "Import Rows from Excel",
                "No data rows were found on worksheet '{}'.".format(sheet_name),
            )
            return

        self._commit_grid()
        if not self._has_meaningful_rows():
            self._table.Clear()

        review_count = 0
        for entry in entries:
            notes = []
            parameter = self._match_parameter(entry["parameter"], entry["shared_group"])
            binding = _excel_binding_value(entry["binding_raw"])
            group = self._match_group(entry["group"])
            category = self._match_category(entry["category"])

            if not parameter:
                if entry["parameter"]:
                    notes.append("Parameter '{}' was not found in the selected shared file".format(entry["parameter"]))
                else:
                    notes.append("Parameter is missing")
            if not binding:
                if entry["binding_raw"]:
                    notes.append("Binding '{}' was not recognized".format(entry["binding_raw"]))
                else:
                    notes.append("Instance/Type is missing")
            if not group:
                if entry["group"]:
                    notes.append("Group '{}' was not recognized".format(entry["group"]))
                else:
                    notes.append("Parameter group is missing")
            if not category:
                if entry["category"]:
                    notes.append("Category '{}' was not recognized".format(entry["category"]))
                else:
                    notes.append("Category is missing")

            note_text = ""
            if notes:
                note_text = "Excel row {}: {}".format(
                    entry["source_row"],
                    "; ".join(notes),
                )
            if note_text:
                review_count += 1
            self._add_row(
                parameter,
                binding,
                group,
                category,
                note_text,
                use_defaults=False,
            )

        self._excel_path = selected
        self.ParameterGrid.ScrollIntoView(self.ParameterGrid.Items[self.ParameterGrid.Items.Count - 1])
        message = "Imported {} row(s) from '{}' in {}.".format(
            len(entries), sheet_name, os.path.basename(selected)
        )
        if review_count:
            message += " {} row(s) need review; use the dropdowns to complete them.".format(review_count)
        else:
            message += " All imported values matched available dropdown options."
        self.StatusText.Text = message

    def _add_click(self, sender, args):
        self._commit_grid()
        self._add_row()
        self.ParameterGrid.ScrollIntoView(self.ParameterGrid.Items[self.ParameterGrid.Items.Count - 1])

    def _duplicate_click(self, sender, args):
        self._commit_grid()
        values = self._selected_values()
        if values is None:
            self.StatusText.Text = "Select a row to duplicate."
            return
        self._add_row(*values)
        self.ParameterGrid.ScrollIntoView(self.ParameterGrid.Items[self.ParameterGrid.Items.Count - 1])

    def _remove_click(self, sender, args):
        selected_items = list(self.ParameterGrid.SelectedItems)
        for selected in selected_items:
            selected.Row.Delete()
        self._table.AcceptChanges()
        if self._table.Rows.Count == 0:
            self._add_row()

    def _cancel_click(self, sender, args):
        self.DialogResult = False

    def _validated_rows(self):
        self._commit_grid()
        valid = []
        errors = []

        for index, row in enumerate(self._table.Rows, start=1):
            parameter_label = _safe_str(row["Parameter"])
            binding_label = _safe_str(row["Binding"])
            group_label = _safe_str(row["Group"])
            category_label = _safe_str(row["Category"])

            if not any((parameter_label, binding_label, group_label, category_label)):
                continue

            missing = []
            if parameter_label not in self._parameter_lookup:
                missing.append("parameter")
            if binding_label not in self._binding_labels:
                missing.append("Instance/Type")
            if group_label not in self._group_lookup:
                missing.append("group")
            if category_label not in self._category_lookup:
                missing.append("category")
            if missing:
                row["ImportNotes"] = "Still required: {}".format(", ".join(missing))
                errors.append("Row {}: select {}.".format(index, ", ".join(missing)))
                continue

            row["ImportNotes"] = ""

            valid.append({
                "parameter_label": parameter_label,
                "definition": self._parameter_lookup[parameter_label],
                "is_instance": binding_label == "Instance",
                "binding_label": binding_label,
                "group_label": group_label,
                "group_id": self._group_lookup[group_label],
                "category_label": category_label,
                "category": self._category_lookup[category_label],
            })

        if not valid and not errors:
            errors.append("Add at least one complete binding row.")
        return valid, errors

    def _import_click(self, sender, args):
        rows, errors = self._validated_rows()
        if errors:
            self.StatusText.Text = "Please complete every row before importing."
            TaskDialog.Show(
                "Bulk Import Shared Parameters",
                "Please correct the following:\n\n{}".format("\n".join(errors[:12])),
            )
            return
        self.result_rows = rows
        self.DialogResult = True


# ---------------------------------------------------------------------------
# Revit binding logic
# ---------------------------------------------------------------------------

def _definition_key(definition):
    try:
        return "guid:" + str(definition.GUID).lower()
    except Exception:
        return "name:" + _safe_str(definition.Name).lower()


def _definition_guid(definition):
    try:
        return str(definition.GUID).lower()
    except Exception:
        return ""


def _definition_id(definition):
    try:
        return _id_value(definition.Id)
    except Exception:
        return None


def _existing_bindings():
    result = {"by_name": {}, "by_id": {}, "by_guid": {}}
    iterator = doc.ParameterBindings.ForwardIterator()
    while iterator.MoveNext():
        definition = iterator.Key
        entry = (definition, iterator.Current)
        name = _safe_str(definition.Name).lower()
        if name:
            result["by_name"][name] = entry
        definition_id = _definition_id(definition)
        if definition_id is not None:
            result["by_id"][definition_id] = entry
        guid = _definition_guid(definition)
        if guid:
            result["by_guid"][guid] = entry
    return result


def _find_existing_binding(definition, bindings):
    """Find the binding for the exact shared GUID and flag same-name conflicts."""
    name = _safe_str(definition.Name).lower()
    guid = _definition_guid(definition)

    if guid and guid in bindings["by_guid"]:
        return bindings["by_guid"][guid], False

    if guid:
        try:
            shared_element = DB.SharedParameterElement.Lookup(doc, definition.GUID)
            if shared_element is not None:
                internal_id = _definition_id(shared_element.GetDefinition())
                if internal_id in bindings["by_id"]:
                    return bindings["by_id"][internal_id], False
        except Exception:
            pass

    same_name = bindings["by_name"].get(name)
    if same_name is not None:
        return None, True
    return None, False


def _group_requested_rows(rows):
    grouped = {}
    keys_by_name = {}
    errors = []
    for row in rows:
        definition = row["definition"]
        key = _definition_key(definition)
        name_key = _safe_str(definition.Name).lower()
        if name_key in keys_by_name and keys_by_name[name_key] != key:
            errors.append(
                "{} refers to more than one shared-parameter GUID.".format(
                    definition.Name
                )
            )
            continue
        keys_by_name[name_key] = key
        signature = (row["is_instance"], row["group_label"])
        if key not in grouped:
            grouped[key] = {
                "definition": definition,
                "parameter_label": row["parameter_label"],
                "is_instance": row["is_instance"],
                "binding_label": row["binding_label"],
                "group_label": row["group_label"],
                "group_id": row["group_id"],
                "categories": {},
            }
        else:
            existing_signature = (
                grouped[key]["is_instance"],
                grouped[key]["group_label"],
            )
            if signature != existing_signature:
                errors.append(
                    "{} uses conflicting Instance/Type or group selections.".format(
                        row["parameter_label"]
                    )
                )
                continue
        category = row["category"]
        grouped[key]["categories"][_id_value(category.Id)] = category
    return list(grouped.values()), errors


def _merge_category_set(existing_binding, requested_categories):
    category_set = app.Create.NewCategorySet()
    try:
        for category in existing_binding.Categories:
            category_set.Insert(category)
    except Exception:
        pass
    for category in requested_categories:
        category_set.Insert(category)
    return category_set


def _run_import(rows):
    grouped, validation_errors = _group_requested_rows(rows)
    if validation_errors:
        TaskDialog.Show(
            "Bulk Import Shared Parameters",
            "Nothing was imported.\n\n{}".format("\n".join(validation_errors)),
        )
        return

    bindings = _existing_bindings()
    transaction = DB.Transaction(doc, "Bulk Import Shared Parameters")
    transaction.Start()

    added = 0
    updated = 0
    skipped = 0
    errors = []
    try:
        for item in grouped:
            definition = item["definition"]
            name = _safe_str(definition.Name)
            existing, name_conflict = _find_existing_binding(definition, bindings)

            try:
                if name_conflict:
                    errors.append("{}: an existing parameter has the same name but a different GUID.".format(name))
                    skipped += 1
                    continue

                if existing:
                    existing_definition, existing_binding = existing
                    if item["is_instance"] and not isinstance(existing_binding, DB.InstanceBinding):
                        errors.append("{}: already bound as Type; requested Instance.".format(name))
                        skipped += 1
                        continue
                    if (not item["is_instance"]) and not isinstance(existing_binding, DB.TypeBinding):
                        errors.append("{}: already bound as Instance; requested Type.".format(name))
                        skipped += 1
                        continue

                    category_set = _merge_category_set(
                        existing_binding,
                        list(item["categories"].values()),
                    )
                    if item["is_instance"]:
                        binding = app.Create.NewInstanceBinding(category_set)
                    else:
                        binding = app.Create.NewTypeBinding(category_set)

                    if doc.ParameterBindings.ReInsert(
                        existing_definition,
                        binding,
                        item["group_id"],
                    ):
                        updated += 1
                    else:
                        errors.append("{}: Revit could not update the existing binding.".format(name))
                        skipped += 1
                else:
                    category_set = _merge_category_set(
                        None,
                        list(item["categories"].values()),
                    )
                    if item["is_instance"]:
                        binding = app.Create.NewInstanceBinding(category_set)
                    else:
                        binding = app.Create.NewTypeBinding(category_set)

                    if doc.ParameterBindings.Insert(definition, binding, item["group_id"]):
                        added += 1
                    else:
                        errors.append("{}: Revit could not create the binding.".format(name))
                        skipped += 1
            except Exception as item_error:
                errors.append("{}: {}".format(name, item_error))
                skipped += 1

        transaction.Commit()
    except Exception:
        transaction.RollBack()
        raise

    category_count = sum(len(item["categories"]) for item in grouped)
    print("Bulk Import Shared Parameters")
    print("Added: {}".format(added))
    print("Updated: {}".format(updated))
    print("Skipped: {}".format(skipped))
    print("Selected category bindings: {}".format(category_count))
    if errors:
        print("Errors:")
        for error in errors:
            print("  {}".format(error))

    message = (
        "Import complete.\n\n"
        "Parameters added: {}\n"
        "Parameters updated: {}\n"
        "Parameters skipped: {}\n"
        "Selected category bindings: {}"
    ).format(added, updated, skipped, category_count)
    if errors:
        message += "\n\nIssues:\n{}".format("\n".join(errors[:8]))
        if len(errors) > 8:
            message += "\n...and {} more. See the pyRevit output.".format(len(errors) - 8)
    TaskDialog.Show("Bulk Import Shared Parameters", message)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _main():
    if doc.IsFamilyDocument:
        TaskDialog.Show(
            "Bulk Import Shared Parameters",
            "This tool creates project parameter bindings and cannot run in a family document.",
        )
        return

    selected_path = _choose_shared_parameter_file(DEFAULT_SHARED_PARAMETERS_PATH)
    if not selected_path:
        return

    try:
        previous_shared_path = _safe_str(app.SharedParametersFilename)
    except Exception:
        previous_shared_path = ""

    try:
        window = BulkImportWindow(selected_path)
        if window.ShowDialog() == True and window.result_rows:
            _run_import(window.result_rows)
    finally:
        try:
            app.SharedParametersFilename = previous_shared_path
        except Exception:
            pass


if __name__ == "__main__":
    try:
        _main()
    except Exception as exc:
        print(traceback.format_exc())
        TaskDialog.Show(
            "Bulk Import Shared Parameters",
            "The import could not be completed.\n\n{}".format(exc),
        )
