# -*- coding: utf-8 -*-
import clr
import datetime
import os
import re
import sys
import traceback

from System.Collections.Generic import List

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit import DB, UI


TITLE = "DirectShape To Mass"
SOURCE_APP_IDS = ("WWPTools.WebContextBuilder", "WWPTools.BuildingImporter")
MAX_FAILURES_IN_REPORT = 30
DEBUG_LOG_FILE_NAME = "DirectShapeToMass.debug.log"
_DEBUG_LOG_BUFFER = []
SOURCE_CATEGORIES = (
    DB.BuiltInCategory.OST_Mass,
    DB.BuiltInCategory.OST_GenericModel,
)
OUTPUT_CATEGORY_OPTIONS = (
    {
        "category": DB.BuiltInCategory.OST_Mass,
        "label": "In-Place Mass (recommended)",
        "use_directshape": True,
        "family_prefix": "WWP_InPlaceMass",
        "result_label": "In-place masses",
    },
    {
        "category": DB.BuiltInCategory.OST_Mass,
        "label": "Mass Family",
        "family_prefix": "WWP_ConvertedMass",
        "result_label": "Mass families",
        "template_keywords": ("conceptual mass", "mass"),
        "exclude_keywords": ("generic model",),
    },
    {
        "category": DB.BuiltInCategory.OST_GenericModel,
        "label": "Generic Model",
        "family_prefix": "WWP_ConvertedGenericModel",
        "result_label": "Generic Model families",
        "template_keywords": ("generic model", "generic models"),
        "exclude_keywords": (
            "face based",
            "ceiling based",
            "floor based",
            "roof based",
            "wall based",
            "line based",
            "pattern based",
            "adaptive",
            "detail item",
            "annotation",
            "profile",
            "tag",
            "titleblock",
        ),
    },
)


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui


def _elem_id_int(eid):
    try:
        return int(eid.Value)      # Revit 2024+
    except AttributeError:
        return int(eid.Value)  # Revit 2023-


def _get_uidoc():
    try:
        return __revit__.ActiveUIDocument
    except Exception:
        return None


def _get_doc():
    uidoc = _get_uidoc()
    return uidoc.Document if uidoc is not None else None


def _set_comment(element, text):
    try:
        parameter = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if parameter and not parameter.IsReadOnly:
            parameter.Set(text)
            return
    except Exception:
        pass

    try:
        parameter = element.LookupParameter("Comments")
        if parameter and not parameter.IsReadOnly:
            parameter.Set(text)
    except Exception:
        pass


def _get_comment(element):
    try:
        parameter = element.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if parameter:
            value = parameter.AsString()
            if value:
                return value
    except Exception:
        pass

    try:
        parameter = element.LookupParameter("Comments")
        if parameter:
            value = parameter.AsString()
            if value:
                return value
    except Exception:
        pass

    return ""


def _get_string_parameter(element, parameter_name):
    try:
        parameter = element.LookupParameter(parameter_name)
        if parameter:
            value = parameter.AsString()
            if value:
                return value
    except Exception:
        pass
    return ""


def _get_exchange_name(element):
    return _get_string_parameter(element, "Exchange Name")


class _SilenceWarningsPreprocessor(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, failuresAccessor):
        for failure in list(failuresAccessor.GetFailureMessages()):
            if failure.GetSeverity() == DB.FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(failure)
        return DB.FailureProcessingResult.Continue


class _OverwriteFamilyLoadOptions(DB.IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        try:
            source.Value = DB.FamilySource.Family
        except Exception:
            pass
        try:
            overwriteParameterValues.Value = True
        except Exception:
            pass
        return True


def _make_safe_name(text, fallback, max_length=80):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "")
    safe = safe.strip("_.")
    if not safe:
        return fallback
    if len(safe) > max_length:
        import hashlib
        h = hashlib.md5(safe.encode("utf-8", errors="replace")).hexdigest()[:8]
        safe = safe[:max_length - 9].rstrip("_.") + "_" + h
    return safe


def _category_display_name(built_in_category):
    if built_in_category == DB.BuiltInCategory.OST_Mass:
        return "Mass"
    if built_in_category == DB.BuiltInCategory.OST_GenericModel:
        return "Generic Model"
    return str(built_in_category)


def _find_family_template(app, output_option):
    template_root = getattr(app, "FamilyTemplatePath", None)
    if not template_root or not os.path.isdir(template_root):
        return None

    candidates = []
    include_keywords = tuple(keyword.lower() for keyword in output_option.get("template_keywords", ()) if keyword)
    exclude_keywords = tuple(keyword.lower() for keyword in output_option.get("exclude_keywords", ()) if keyword)
    for root, _, files in os.walk(template_root):
        for file_name in files:
            lowered_name = file_name.lower()
            if not lowered_name.endswith(".rft"):
                continue
            lowered_path = os.path.join(root, file_name).lower()
            if include_keywords and not any(keyword in lowered_path for keyword in include_keywords):
                continue
            if exclude_keywords and any(keyword in lowered_path for keyword in exclude_keywords):
                continue
            full_path = os.path.join(root, file_name)
            score = 100
            if "conceptual mass" in lowered_path:
                score = 0
            elif "metric generic model" in lowered_path:
                score = 5
            elif "metric" in lowered_name and "mass" in lowered_name:
                score = 10
            elif "metric" in lowered_name and "generic model" in lowered_name:
                score = 10
            elif lowered_name == "mass.rft":
                score = 20
            elif lowered_name == "generic model.rft":
                score = 20
            candidates.append((score, len(full_path), full_path))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][2]


def _ensure_cache_dir():
    cache_dir = os.path.join(
        os.getenv("APPDATA") or os.path.expanduser("~"),
        "pyRevit",
        "WWPTools",
        "DirectShapeToMassCache",
    )
    if not os.path.isdir(cache_dir):
        os.makedirs(cache_dir)
    return cache_dir


def _debug_log_path():
    return os.path.join(_ensure_cache_dir(), DEBUG_LOG_FILE_NAME)


def _reset_debug_log():
    global _DEBUG_LOG_BUFFER
    _DEBUG_LOG_BUFFER = ["[{}] {} debug session started".format(datetime.datetime.now().isoformat(), TITLE)]
    return _debug_log_path()


def _log_debug(message):
    global _DEBUG_LOG_BUFFER
    _DEBUG_LOG_BUFFER.append("[{}] {}".format(datetime.datetime.now().isoformat(), message))


def _read_debug_log():
    try:
        return "\n".join(_DEBUG_LOG_BUFFER).strip()
    except Exception:
        return ""


def _flush_debug_log():
    log_path = _debug_log_path()
    try:
        with open(log_path, "w") as log_file:
            log_file.write(_read_debug_log() + "\n")
    except Exception:
        pass
    return log_path


def _is_target_category(element):
    try:
        category_id_value = _elem_id_int(element.Category.Id)
        return category_id_value in [int(category) for category in SOURCE_CATEGORIES]
    except Exception:
        return False


def _is_supported_import(element, allow_any_directshape=False):
    if element is None:
        return False
    if not _is_target_category(element):
        return False
    # Skip elements that were already produced by a previous run of this tool
    if _get_comment(element).startswith("Converted from DirectShape"):
        return False
    if allow_any_directshape:
        return True

    app_id = (getattr(element, "ApplicationId", None) or "").strip()
    if app_id in SOURCE_APP_IDS:
        return True

    if _get_exchange_name(element):
        return True

    comment = _get_comment(element)
    return comment.startswith("Web Context Builder |") or comment.startswith("OSM feature:")


def _iter_category_elements(doc):
    try:
        category_filter = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](list(SOURCE_CATEGORIES)))
        collector = DB.FilteredElementCollector(doc).WhereElementIsNotElementType().WherePasses(category_filter)
        return list(collector)
    except Exception:
        elements = []
        for element in DB.FilteredElementCollector(doc).WhereElementIsNotElementType():
            if _is_target_category(element):
                elements.append(element)
        return elements


def _mesh_to_solid(mesh):
    """Try to convert a DB.Mesh to a DB.Solid using TessellatedShapeBuilder."""
    try:
        builder = DB.TessellatedShapeBuilder()
        builder.OpenConnectedFaceSet(False)
        for i in range(mesh.NumTriangles):
            try:
                tri = mesh.get_Triangle(i)
                pts = List[DB.XYZ]()  # type: ignore[misc]
                pts.Add(tri.get_Vertex(0))  # type: ignore[union-attr]
                pts.Add(tri.get_Vertex(1))  # type: ignore[union-attr]
                pts.Add(tri.get_Vertex(2))  # type: ignore[union-attr]
                builder.AddFace(DB.TessellatedFace(pts, DB.ElementId.InvalidElementId))  # type: ignore[arg-type]
            except Exception:
                pass
        builder.CloseConnectedFaceSet()
        builder.Target = DB.TessellatedShapeBuilderTarget.Solid  # type: ignore[misc]
        builder.Fallback = DB.TessellatedShapeBuilderFallback.Mesh  # type: ignore[misc]
        result = builder.Build()
        if result is None:
            return None
        for geo_obj in result.GetGeometricalObjects():  # type: ignore[union-attr]
            if isinstance(geo_obj, DB.Solid):
                try:
                    if abs(geo_obj.Volume) > 1e-9 and geo_obj.Faces.Size > 0:
                        return geo_obj
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _iter_solids(geometry_element):
    solids = []
    fallback_meshes = []
    if geometry_element is None:
        return solids

    for geometry_object in geometry_element:
        if isinstance(geometry_object, DB.Solid):
            try:
                if geometry_object.Volume > 1e-9 and geometry_object.Faces.Size > 0:
                    solids.append(geometry_object)
            except Exception:
                pass
        elif isinstance(geometry_object, DB.Mesh):
            fallback_meshes.append(geometry_object)
        elif isinstance(geometry_object, DB.GeometryInstance):
            try:
                solids.extend(_iter_solids(geometry_object.GetInstanceGeometry()))
            except Exception:
                pass

    # No Brep solids found - try converting any meshes to solids
    if not solids:
        for mesh in fallback_meshes:
            solid = _mesh_to_solid(mesh)
            if solid is not None:
                solids.append(solid)

    return solids


def _get_element_solids(element):
    options = DB.Options()
    try:
        options.IncludeNonVisibleObjects = True
    except Exception:
        pass

    geometry = element.get_Geometry(options)
    solids = _iter_solids(geometry)
    solids.sort(key=lambda solid: solid.Volume, reverse=True)

    # Deduplicate solids with nearly identical volumes (same geometry instanced multiple times)
    deduplicated = []
    seen_volumes = []
    for solid in solids:
        vol = solid.Volume
        if not any(abs(vol - v) < 1e-6 for v in seen_volumes):
            deduplicated.append(solid)
            seen_volumes.append(vol)
    return deduplicated


def _get_candidate_elements(doc, uidoc):
    selected = []
    try:
        for element_id in uidoc.Selection.GetElementIds():
            element = doc.GetElement(element_id)
            if _is_supported_import(element, allow_any_directshape=True):
                selected.append(element)
    except Exception:
        selected = []

    if selected:
        _log_debug("Using {} selected source element(s).".format(len(selected)))
        return selected, "Current selection"

    imported = []
    fallback = []
    for element in _iter_category_elements(doc):
        fallback.append(element)
        if _is_supported_import(element, allow_any_directshape=False):
            imported.append(element)

    if imported:
        _log_debug("Using {} imported source element(s).".format(len(imported)))
        return imported, "Imported Web Context / Building Importer / Data Exchange elements"

    _log_debug("No tagged imports found. Falling back to {} element(s) from Mass / Generic Models.".format(len(fallback)))
    return fallback, "All elements in Mass / Generic Models"


def _prompt_output_category():
    labels = [option["label"] for option in OUTPUT_CATEGORY_OPTIONS]
    selected = ui.uiUtils_select_indices(
        labels,
        title=TITLE,
        prompt="Select the output category for converted families:",
        multiselect=False,
        width=420,
        height=260,
    )
    if not selected:
        return None
    return OUTPUT_CATEGORY_OPTIONS[selected[0]]


def _load_family_into_project(doc, family_path, family_name):
    _log_debug("Loading family into project. path='{}' expected_name='{}'".format(family_path, family_name))
    _log_debug("Project doc.IsModifiable before load: {}".format(getattr(doc, "IsModifiable", "<unknown>")))
    before_family_ids = set()
    try:
        for candidate in DB.FilteredElementCollector(doc).OfClass(DB.Family):
            before_family_ids.add(_elem_id_int(candidate.Id))
    except Exception:
        pass
    _log_debug("Project family count before load: {}".format(len(before_family_ids)))

    # Caller is responsible for the active transaction.
    load_error = None
    family = None

    load_attempts = [
        ("doc.LoadFamily(path)", lambda: doc.LoadFamily(family_path)),
        ("doc.LoadFamily(path, options)", lambda: doc.LoadFamily(family_path, _OverwriteFamilyLoadOptions())),
    ]
    for label, attempt in load_attempts:
        try:
            loaded = attempt()
            _log_debug("{} returned type='{}' value='{}'".format(label, type(loaded).__name__, loaded))
            if isinstance(loaded, DB.Family):
                family = loaded
                break
            if isinstance(loaded, (list, tuple)):
                for item in loaded:
                    if isinstance(item, DB.Family):
                        family = item
                        break
                if family is not None:
                    break
        except Exception as ex:
            load_error = str(ex)
            _log_debug("{} raised: {}".format(label, load_error))

    try:
        if family is None:
            family_name_stem = os.path.splitext(os.path.basename(family_path))[0]
            exact_matches = []
            stem_matches = []
            prefix_matches = []
            new_families = []

            for candidate in DB.FilteredElementCollector(doc).OfClass(DB.Family):
                try:
                    candidate_name = candidate.Name or ""
                    if _elem_id_int(candidate.Id) not in before_family_ids:
                        new_families.append(candidate)
                    if candidate_name == family_name:
                        exact_matches.append(candidate)
                    elif candidate_name == family_name_stem:
                        stem_matches.append(candidate)
                    elif candidate_name.startswith(family_name) or candidate_name.startswith(family_name_stem):
                        prefix_matches.append(candidate)
                except Exception:
                    pass

            _log_debug(
                "Family lookup after load: exact_matches={} stem_matches={} prefix_matches={} new_families={} stem='{}'".format(
                    len(exact_matches),
                    len(stem_matches),
                    len(prefix_matches),
                    len(new_families),
                    family_name_stem,
                )
            )
            if new_families:
                _log_debug("New family names: {}".format(", ".join(sorted([(candidate.Name or "<unnamed>") for candidate in new_families][:20]))))

            if exact_matches:
                family = exact_matches[0]
            elif stem_matches:
                family = stem_matches[0]
            elif len(new_families) == 1:
                family = new_families[0]
            elif prefix_matches:
                family = prefix_matches[0]
    except Exception:
        pass

    if family is not None:
        try:
            _log_debug("Resolved loaded family: id={} name='{}'".format(_elem_id_int(family.Id), family.Name or "<unnamed>"))
        except Exception:
            _log_debug("Resolved loaded family object.")
    else:
        _log_debug("Failed to resolve loaded family object after load.")

    return family, load_error


def _get_solid_base_point(solids):
    """Return the bottom-centre XY point of the largest solid, at Z=0, for instance placement."""
    try:
        solid = solids[0]
        bb = solid.GetBoundingBox()
        if bb is not None:
            mid_x = (bb.Min.X + bb.Max.X) * 0.5
            mid_y = (bb.Min.Y + bb.Max.Y) * 0.5
            return DB.XYZ(mid_x, mid_y, 0.0)
    except Exception:
        pass
    return DB.XYZ(0.0, 0.0, 0.0)


def _place_family_instance(doc, family, comment_text, placement_point=None):
    if placement_point is None:
        placement_point = DB.XYZ(0.0, 0.0, 0.0)
    try:
        _log_debug("Placing family instance for family id={} name='{}'".format(_elem_id_int(family.Id), family.Name or "<unnamed>"))
    except Exception:
        _log_debug("Placing family instance for unresolved family metadata.")
    symbol_ids = list(family.GetFamilySymbolIds())
    if not symbol_ids:
        raise Exception("The loaded family contains no types.")

    symbol = doc.GetElement(symbol_ids[0])
    if symbol is None:
        raise Exception("Failed to access the loaded family type.")

    # Caller is responsible for the active transaction.
    try:
        if hasattr(symbol, "IsActive") and not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass

    pt = placement_point
    creation_attempts = [
        lambda: doc.Create.NewFamilyInstance(pt, symbol, DB.Structure.StructuralType.NonStructural),
        lambda: doc.Create.NewFamilyInstance(pt, symbol, doc.ActiveView),
        lambda: doc.Create.NewFamilyInstance(pt, symbol, doc.GetElement(doc.ActiveView.GenLevel.Id), DB.Structure.StructuralType.NonStructural),
    ]
    errors = []
    for attempt in creation_attempts:
        try:
            instance = attempt()
            if instance is not None:
                _set_comment(instance, comment_text)
                _log_debug("Placed family instance id={}".format(_elem_id_int(instance.Id)))
                return instance
        except Exception as ex:
            errors.append(str(ex))
            _log_debug("Family placement attempt failed: {}".format(str(ex)))

    raise Exception("Failed to place the generated mass family instance. {}".format("; ".join(errors[:5])))


def _add_material_instance_param(family_doc, freeform_elements):
    """Add a 'Material' instance parameter to the family and associate it with the primary freeform geometry."""
    if not freeform_elements:
        return

    family_manager = family_doc.FamilyManager
    mat_family_param = None

    # Revit 2022+ API uses ForgeTypeId-based overload
    try:
        mat_family_param = family_manager.AddParameter(
            "Material",
            DB.GroupTypeId.Materials,
            DB.SpecTypeId.Reference.Material,
            True,  # instance parameter
        )
    except Exception:
        pass

    # Fallback for pre-2022 Revit (ParameterType enum)
    if mat_family_param is None:
        try:
            mat_family_param = family_manager.AddParameter(
                "Material",
                DB.BuiltInParameterGroup.PG_MATERIALS,
                DB.ParameterType.Material,
                True,  # instance parameter
            )
        except Exception:
            pass

    if mat_family_param is None:
        _log_debug("Could not add Material family instance parameter - skipping association.")
        return

    # Associate the largest solid's freeform element material parameter to the family parameter
    freeform = freeform_elements[0]
    mat_elem_param = freeform.get_Parameter(DB.BuiltInParameter.MATERIAL_ID_PARAM)
    if mat_elem_param is None:
        mat_elem_param = freeform.LookupParameter("Material")

    if mat_elem_param is not None:
        try:
            family_manager.AssociateElementParameterToFamilyParameter(mat_elem_param, mat_family_param)
            _log_debug("Associated freeform material parameter to family instance parameter 'Material'.")
        except Exception as ex:
            _log_debug("Could not associate material parameter: {}".format(ex))
    else:
        _log_debug("No material parameter found on freeform element - association skipped.")


def _convert_element_to_directshape(doc, element, solids, output_option, seen_family_names=None):
    # Caller is responsible for the active transaction.
    _log_debug("Converting source element id={} app_id='{}' app_data='{}' solids={} → DirectShape".format(
        _elem_id_int(element.Id),
        (getattr(element, "ApplicationId", None) or ""),
        (getattr(element, "ApplicationDataId", None) or ""),
        len(solids),
    ))
    elem_id = _elem_id_int(element.Id)

    source_key = _get_string_parameter(element, "IfcGUID")
    if not source_key:
        source_key = _get_string_parameter(element, "IFC GUID")
    if not source_key:
        source_key = _get_exchange_name(element)
    if not source_key:
        source_key = (getattr(element, "ApplicationDataId", None) or "").strip()
    if not source_key:
        try:
            type_id = element.GetTypeId()
            if type_id is not None and type_id != DB.ElementId.InvalidElementId:
                type_elem = doc.GetElement(type_id)
                if type_elem is not None:
                    source_key = (type_elem.Name or "").strip()
        except Exception:
            pass
    if not source_key:
        source_key = "Element_{}".format(elem_id)

    family_prefix = output_option.get("family_prefix") or "WWP_InPlaceMass"
    display_name = _make_safe_name("{}_{}".format(family_prefix, source_key), "{}_{}".format(family_prefix, elem_id))

    if seen_family_names is not None and display_name in seen_family_names:
        display_name = _make_safe_name(
            "{}_{}".format(display_name, elem_id),
            "{}_{}".format(family_prefix, elem_id),
        )
    if seen_family_names is not None:
        seen_family_names.add(display_name)

    built_in_category = output_option.get("category") or DB.BuiltInCategory.OST_Mass
    comment_text = "Converted from DirectShape {}{}".format(
        elem_id,
        " | {}".format(_get_comment(element)) if _get_comment(element) else "",
    )

    category_id = DB.ElementId(built_in_category)
    ds = DB.DirectShape.CreateElement(doc, category_id)
    ds.ApplicationId = "WWPTools.DirectShapeToMass"
    ds.ApplicationDataId = source_key

    geometry_objects = List[DB.GeometryObject]()
    for solid in solids:
        geometry_objects.Add(solid)
    ds.SetShape(geometry_objects)
    _set_comment(ds, comment_text)

    try:
        ds.Name = display_name
    except Exception:
        pass

    _log_debug("Created in-place mass id={} name='{}'".format(_elem_id_int(ds.Id), display_name))
    return ds


def _convert_element_to_family(doc, element, solids, output_option, seen_family_names=None):
    _log_debug("Converting source element id={} type='{}' app_id='{}' app_data='{}' solids={}".format(
        _elem_id_int(element.Id),
        element.GetType().FullName if element is not None else "<none>",
        (getattr(element, "ApplicationId", None) or ""),
        (getattr(element, "ApplicationDataId", None) or ""),
        len(solids),
    ))
    app = getattr(doc, "Application", None)
    if app is None:
        raise Exception("Unable to access the Revit application.")

    template_path = _find_family_template(app, output_option)
    if not template_path:
        template_root = getattr(app, "FamilyTemplatePath", None) or "<not set>"
        raise Exception(
            "No {} family template was found under Revit's Family Template Path.\n"
            "Current path: {}\n"
            "Set Revit's Family Template Path to a location that contains a suitable {} template and try again."
            .format(_category_display_name(output_option["category"]), template_root, _category_display_name(output_option["category"]))
        )
    _log_debug("Using {} family template: {}".format(_category_display_name(output_option["category"]), template_path))

    elem_id = _elem_id_int(element.Id)

    # Prefer IFC GUID (unique per element on IFC imports), then Exchange Name,
    # then ApplicationDataId, then type name, then fall back to element ID.
    source_key = _get_string_parameter(element, "IfcGUID")
    if not source_key:
        source_key = _get_string_parameter(element, "IFC GUID")
    if not source_key:
        source_key = _get_exchange_name(element)
    if not source_key:
        source_key = (getattr(element, "ApplicationDataId", None) or "").strip()
    if not source_key:
        try:
            type_id = element.GetTypeId()
            if type_id is not None and type_id != DB.ElementId.InvalidElementId:
                type_elem = doc.GetElement(type_id)
                if type_elem is not None:
                    source_key = (type_elem.Name or "").strip()
        except Exception:
            pass
    if not source_key:
        source_key = "Element_{}".format(elem_id)

    family_prefix = output_option.get("family_prefix") or "WWP_ConvertedFamily"
    family_name = _make_safe_name("{}_{}".format(family_prefix, source_key), "{}_{}".format(family_prefix, elem_id))

    # If another element in this run already claimed this name (e.g. Revit Parts that
    # share a parent's IfcGUID/ApplicationDataId, or elements sharing a type name),
    # append the element ID so each element gets its own distinct .rfa and project family.
    if seen_family_names is not None and family_name in seen_family_names:
        family_name = _make_safe_name(
            "{}_{}".format(family_name, elem_id),
            "{}_{}".format(family_prefix, elem_id),
        )
    if seen_family_names is not None:
        seen_family_names.add(family_name)

    family_doc = app.NewFamilyDocument(template_path)
    if family_doc is None:
        raise Exception("Failed to open a new {} family document.".format(_category_display_name(output_option["category"])))
    _log_debug("Opened new family document for '{}'.".format(family_name))

    family_path = os.path.join(_ensure_cache_dir(), "{}.rfa".format(family_name))
    transaction = None
    try:
        transaction = DB.Transaction(family_doc, "Create Converted {}".format(_category_display_name(output_option["category"])))
        transaction.Start()

        owner_family = getattr(family_doc, "OwnerFamily", None)
        if owner_family is not None:
            try:
                owner_family.Name = family_name
            except Exception:
                pass

        freeform_cls = getattr(DB, "FreeFormElement", None)
        if freeform_cls is None:
            raise Exception("This Revit version does not support FreeFormElement creation.")

        source_comment = _get_comment(element)
        created_freeforms = []
        for solid in solids:
            freeform = freeform_cls.Create(family_doc, solid)
            if freeform is None:
                raise Exception("Revit did not create a freeform element in the {} family.".format(_category_display_name(output_option["category"])))
            _set_comment(freeform, source_comment)
            created_freeforms.append(freeform)

        _add_material_instance_param(family_doc, created_freeforms)

        transaction.Commit()
        transaction = None

        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_doc.SaveAs(family_path, save_options)
        _log_debug("Saved generated family to '{}'.".format(family_path))
    except Exception:
        if transaction is not None:
            try:
                transaction.RollBack()
            except Exception:
                pass
        _log_debug("Conversion failed before family load:\n{}".format(traceback.format_exc()))
        raise
    finally:
        try:
            family_doc.Close(False)
        except Exception:
            pass
        _log_debug("Closed family document for '{}'.".format(family_name))

    family, load_error = _load_family_into_project(doc, family_path, family_name)
    if family is None:
        if load_error:
            raise Exception("Failed to load the generated {} family into the project. {}".format(_category_display_name(output_option["category"]), load_error))
        raise Exception("Failed to load the generated {} family into the project.".format(_category_display_name(output_option["category"])))

    comment_text = "Converted from DirectShape {}{}".format(
        _elem_id_int(element.Id),
        " | {}".format(_get_comment(element)) if _get_comment(element) else "",
    )
    placement_point = _get_solid_base_point(solids)
    return _place_family_instance(doc, family, comment_text, placement_point)


def _get_solid_fill_pattern_id(doc):
    try:
        for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
            try:
                if fp.GetFillPattern().IsSolidFill:
                    return fp.Id
            except Exception:
                pass
    except Exception:
        pass
    return DB.ElementId.InvalidElementId


def _highlight_failed_elements(doc, failed_element_ids):
    if not failed_element_ids:
        return
    try:
        active_view = doc.ActiveView
        if active_view is None:
            return
        red = DB.Color(255, 0, 0)
        solid_fill_id = _get_solid_fill_pattern_id(doc)
        override = DB.OverrideGraphicSettings()
        override.SetProjectionLineColor(red)
        override.SetSurfaceForegroundPatternColor(red)
        override.SetCutForegroundPatternColor(red)
        if solid_fill_id != DB.ElementId.InvalidElementId:
            override.SetSurfaceForegroundPatternId(solid_fill_id)
            override.SetCutForegroundPatternId(solid_fill_id)
        transaction = DB.Transaction(doc, "Highlight Failed Elements")
        transaction.Start()
        for eid in failed_element_ids:
            try:
                active_view.SetElementOverrides(eid, override)
            except Exception:
                pass
        transaction.Commit()
        _log_debug("Applied red highlight to {} failed element(s).".format(len(failed_element_ids)))
    except Exception as ex:
        _log_debug("Could not apply failure highlights: {}".format(ex))


def _preview_text(scope_label, elements, output_option):
    lines = [
        "Scope: {}".format(scope_label),
        "Source elements found: {}".format(len(elements)),
        "Supported categories: Mass, Generic Models",
        "Output category: {}".format(_category_display_name(output_option["category"])),
        "Output: one {} per source element".format((output_option.get("result_label") or "family").rstrip("s").lower()),
        "Original source elements will be kept.",
        "",
        "Preview of first 20 source elements:",
    ]

    for element in elements[:20]:
        lines.append(
            "Element {} | {} | {} | Type: {}{}".format(
                _elem_id_int(element.Id),
                (getattr(element, "ApplicationDataId", None) or "<no app data id>"),
                _get_comment(element) or "<no comment>",
                element.GetType().Name if element is not None else "<unknown>",
                " | Exchange Name: {}".format(_get_exchange_name(element)) if _get_exchange_name(element) else "",
            )
        )

    if len(elements) > 20:
        lines.append("...")

    return "\n".join(lines)


def _summarize_results(created_count, failures, output_option):
    lines = [
        "{} created: {}".format(output_option.get("result_label") or "Families", created_count),
        "Failures: {}".format(len(failures)),
    ]

    if failures:
        lines.append("")
        lines.append("Failures (first {}):".format(MAX_FAILURES_IN_REPORT))
        for failure in failures[:MAX_FAILURES_IN_REPORT]:
            lines.append("{} | {}".format(failure["id"], failure["reason"]))

    return "\n".join(lines)


def main():
    uidoc = _get_uidoc()
    doc = _get_doc()
    if uidoc is None or doc is None:
        UI.TaskDialog.Show(TITLE, "No active Revit document found.")
        return

    log_path = _reset_debug_log()
    _log_debug("Active document title='{}' version='{}'".format(getattr(doc, "Title", "<unknown>"), getattr(doc.Application, "VersionNumber", "<unknown>")))

    elements, scope_label = _get_candidate_elements(doc, uidoc)
    if not elements:
        UI.TaskDialog.Show(
            TITLE,
            "No matching elements were found in the Mass or Generic Models categories.\n"
            "Select the source elements manually, or run this after Web Context Builder / Building Importer / Data Exchange.",
        )
        return

    output_option = _prompt_output_category()
    if output_option is None:
        return

    if not ui.uiUtils_show_text_report(
        "{} - Preview".format(TITLE),
        _preview_text(scope_label, elements, output_option),
        ok_text="Convert",
        cancel_text="Cancel",
        width=760,
        height=560,
    ):
        return

    created_instances = []
    failures = []
    failed_element_ids = []
    seen_family_names = set()

    # Extract all geometry BEFORE placing any families.
    # Placing a family commits a transaction and triggers a model regeneration, which
    # causes Revit to include the newly placed masses in subsequent elements' geometry
    # trees - producing N copies for the Nth building processed.
    element_solids = []
    for element in elements:
        try:
            solids = _get_element_solids(element)
            if not solids:
                raise Exception("No valid solids were found in the source element geometry.")
            _log_debug("Element {} yielded {} solid(s).".format(_elem_id_int(element.Id), len(solids)))
            element_solids.append((element, solids))
        except Exception as ex:
            _log_debug("Element {} solid extraction failed: {}\n{}".format(_elem_id_int(element.Id), str(ex), traceback.format_exc()))
            failures.append({"id": "element/{}".format(_elem_id_int(element.Id)), "reason": str(ex)})
            failed_element_ids.append(element.Id)

    def _start_tx(name):
        tx = DB.Transaction(doc, name)
        fho = tx.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_SilenceWarningsPreprocessor())
        tx.SetFailureHandlingOptions(fho)
        tx.Start()
        return tx

    convert_fn = _convert_element_to_directshape if output_option.get("use_directshape") else _convert_element_to_family
    tx_name = "Create In-Place Masses" if output_option.get("use_directshape") else "Create Mass Families"
    tx = _start_tx(tx_name)
    for element, solids in element_solids:
        try:
            instance = convert_fn(doc, element, solids, output_option, seen_family_names)
            if instance is not None:
                created_instances.append(instance)
        except Exception as ex:
            _log_debug("Element {} failed: {}\n{}".format(_elem_id_int(element.Id), str(ex), traceback.format_exc()))
            failures.append({"id": "element/{}".format(_elem_id_int(element.Id)), "reason": str(ex)})
            failed_element_ids.append(element.Id)
    tx.Commit()

    _highlight_failed_elements(doc, failed_element_ids)

    try:
        if created_instances:
            selected_ids = List[DB.ElementId]()
            for instance in created_instances:
                selected_ids.Add(instance.Id)
            uidoc.Selection.SetElementIds(selected_ids)
    except Exception:
        pass

    result_text = _summarize_results(len(created_instances), failures, output_option)
    if failures:
        log_path = _flush_debug_log()
        result_text += "\n\nDetailed debug log:\n{}".format(log_path)

    ui.uiUtils_show_text_report(
        "{} - Results".format(TITLE),
        result_text,
        ok_text="Close",
        cancel_text=None,
        width=760,
        height=520,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        UI.TaskDialog.Show(TITLE + " - Error", "{}\n\n{}".format(exc, traceback.format_exc()))
