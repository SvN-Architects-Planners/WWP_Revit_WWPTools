"""Erase selected data schema and its entities."""

from pyrevit import revit, DB
from WWP_uiUtils import uiUtils_alert, uiUtils_confirm, uiUtils_select_indices

doc = revit.doc

def _access_level_text(level):
    return str(level) if level is not None else "Unknown"

def _vendor_text(schema):
    vendor = getattr(schema, "VendorId", None)
    return vendor if vendor else "None"

def _app_guid_text(schema):
    guid = getattr(schema, "ApplicationGUID", None)
    return str(guid) if guid else "None"

def _is_protected(schema):
    return schema.WriteAccessLevel != DB.ExtensibleStorage.AccessLevel.Public

def _schema_display(schema):
    prefix = "[PROTECTED] " if _is_protected(schema) else ""
    return "{}{} ({}) | R:{} W:{} | Vendor:{} | App:{}".format(
        prefix,
        schema.SchemaName,
        schema.GUID,
        _access_level_text(schema.ReadAccessLevel),
        _access_level_text(schema.WriteAccessLevel),
        _vendor_text(schema),
        _app_guid_text(schema)
    )

def _select_schemas(schemas):
    display = [_schema_display(schema) for schema in schemas]
    indices = uiUtils_select_indices(
        display,
        title="Wipe Extensible Storage",
        prompt="Select schemas to remove (protected schemas are flagged):"
    )
    return [schemas[i] for i in indices]


schemas = DB.ExtensibleStorage.Schema.ListSchemas()

sschemas = _select_schemas(schemas) or []

if sschemas:
    protected = [x for x in sschemas if _is_protected(x)]
    if protected:
        msg = "Skipping protected schemas (insufficient write access):\n\n"
        msg += "\n".join(
            "- {} ({})".format(x.SchemaName, x.GUID) for x in protected
        )
        uiUtils_alert(msg, "Wipe Extensible Storage")
    sschemas = [x for x in sschemas if x not in protected]
    if sschemas:
        confirm = uiUtils_confirm(
            "Delete the selected schemas and all stored entities?\n\n"
            "This cannot be undone.",
            "Wipe Extensible Storage"
        )
        if not confirm:
            sschemas = []

removed = []
failed = []

for sschema in sschemas:
    with revit.Transaction("Remove Schema"):
        try:
            doc.EraseSchemaAndAllEntities(sschema)
            if DB.ExtensibleStorage.Schema.Lookup(sschema.GUID) is None:
                removed.append(sschema)
            else:
                failed.append(sschema)
        except Exception as e:
            print(e)
            failed.append(sschema)

if sschemas:
    if failed:
        msg = "Some schemas could not be removed:\n\n"
        msg += "\n".join(
            "- {} ({})".format(x.SchemaName, x.GUID) for x in failed
        )
        if removed:
            msg += "\n\nRemoved:\n"
            msg += "\n".join(
                "- {} ({})".format(x.SchemaName, x.GUID) for x in removed
            )
        uiUtils_alert(msg, "Wipe Extensible Storage")
    else:
        msg = "Removed:\n\n"
        msg += "\n".join(
            "- {} ({})".format(x.SchemaName, x.GUID) for x in removed
        )
        uiUtils_alert(msg, "Wipe Extensible Storage")
