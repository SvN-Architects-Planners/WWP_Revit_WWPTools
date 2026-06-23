import clr
clr.AddReference('System')
clr.AddReference('System.Xml')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
import hashlib
import json
import math
import os
import re
import sys
import traceback
import xml.etree.ElementTree as ET

from System import Uri
from System.Collections.Generic import List
from System.IO import File, StringReader
from System.Windows import Visibility
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media.Imaging import BitmapCacheOption, BitmapImage
from System.Xml import XmlReader

clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")

from Autodesk.Revit import DB, UI

from WWP_versioning import apply_window_title


def _el_name(el):
    try:
        return el.Name or ''
    except Exception:
        return ''


TITLE = "UK Context Builder"
APP_ID = "WWPTools.UKContextBuilder"
DEFAULT_RADIUS_M = 500.0
MIN_RADIUS_M = 50.0
MAX_RADIUS_M = 2000.0
USER_AGENT = "WWPTools.UKContextBuilder/1.0"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://z.overpass-api.de/api/interpreter",
]
PEDESTRIAN_HIGHWAYS = (
    "footway",
    "path",
    "cycleway",
    "pedestrian",
    "steps",
    "track",
    "bridleway",
    "corridor",
)
MAX_FAILURES_IN_REPORT = 30
MAP_CLICK_SCHEME = "pyrevit-map://click"
UK_TERRAIN_DTM_WMS_ENDPOINT = "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wms"
UK_TERRAIN_DTM_WMS_LAYER = "Lidar_Composite_Elevation_DTM_1m"
UK_TERRAIN_DSM_WMS_ENDPOINT = "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-last-return-dsm-1m/wms"
UK_TERRAIN_DSM_WMS_LAYER = "Lidar_Composite_Elevation_LZ_DSM_1m"
TERRAIN_MIN_GRID_SPACING_M = 60.0
TERRAIN_DENSE_SQUARE_SIZE_M = 100.0
TERRAIN_DENSE_GRID_SPACING_M = 10.0
TERRAIN_SAMPLE_WINDOW_M = 12.0
MAX_TERRAIN_SAMPLE_POINTS = 121
BASE_TOTAL_TERRAIN_SAMPLE_POINTS = 260
MAX_TOTAL_TERRAIN_SAMPLE_POINTS = 1600
MAX_DENSE_GRID_SAMPLE_POINTS = 1521
MIN_DENSE_AREA_M = 20.0
DSM_DTM_MIN_BUILDING_HEIGHT_M = 3.0
DSM_DTM_MAX_BUILDING_HEIGHT_M = 120.0
DSM_DTM_SAMPLE_POINT_LIMIT = 9
DSM_DTM_DRYRUN_LIMIT = 12
FAST_TERRAIN_MIN_GRID_SPACING_M = 120.0
FAST_MAX_TERRAIN_SAMPLE_POINTS = 49
FAST_BASE_TOTAL_TERRAIN_SAMPLE_POINTS = 81
FAST_MAX_TOTAL_TERRAIN_SAMPLE_POINTS = 121
MAX_BUILDING_SOLIDS_PER_MASS_FAMILY = 1
BUILDING_OUTPUT_DIRECTSHAPE = "directshape"
BUILDING_OUTPUT_INPLACE_MASS = "inplacemass"
BUILDING_OUTPUT_MASS_FAMILY = "massfamily"
TERRAIN_SUBDIVISION_OFFSET_2026_M = -0.15
CONTEXT_FLOOR_THICKNESS_M = 0.01
CONTEXT_PALETTE = {
    "buildings": {"name": "WWP Context - Massing", "rgb": (186, 186, 186), "transparency": 0},
    "roads": {"name": "WWP Context - Road", "rgb": (152, 152, 152), "transparency": 0},
    "tracks": {"name": "WWP Context - Track", "rgb": (120, 120, 120), "transparency": 0},
    "parcels": {"name": "WWP Context - Parcel", "rgb": (255, 221, 21), "transparency": 60},
    "parks": {"name": "WWP Context - Park", "rgb": (143, 188, 143), "transparency": 0},
    "water": {"name": "WWP Context - Water", "rgb": (173, 216, 230), "transparency": 0},
    "terrain": {"name": "WWP Context - Curb", "rgb": (225, 225, 225), "transparency": 0},
}
CACHE_VERSION = "v1"
CACHE_ROOT = os.path.join(
    os.getenv("APPDATA") or os.path.expanduser("~"),
    "pyRevit",
    "WWPTools",
    "UKContextBuilderCache",
    CACHE_VERSION,
)
SETTINGS_ROOT = os.path.join(
    os.getenv("APPDATA") or os.path.expanduser("~"),
    "pyRevit",
    "WWPTools",
)
SETTINGS_FILE_PATH = os.path.join(SETTINGS_ROOT, "UKContextBuilderSettings.json")


script_dir = os.path.dirname(__file__)
lib_path = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "lib"))
if lib_path not in sys.path:
    sys.path.append(lib_path)

import WWP_uiUtils as ui
from WWP_compat import Request, decode_to_text, urllib_parse, urlopen


def _get_doc():
    try:
        uidoc = __revit__.ActiveUIDocument
        if uidoc is None:
            return None
        return uidoc.Document
    except Exception:
        return None


def _set_owner(window):
    try:
        helper = WindowInteropHelper(window)
        helper.Owner = __revit__.ActiveUIDocument.Application.MainWindowHandle
    except Exception:
        pass


def _load_logo(image_control):
    try:
        logo_path = os.path.join(lib_path, "WWPtools-logo.png")
        if image_control is None or not os.path.isfile(logo_path):
            return
        bitmap = BitmapImage()
        bitmap.BeginInit()
        bitmap.UriSource = Uri(logo_path)
        bitmap.CacheOption = BitmapCacheOption.OnLoad
        bitmap.EndInit()
        image_control.Source = bitmap
    except Exception:
        pass


def _ensure_cache_dir(bucket_name):
    bucket_path = os.path.join(CACHE_ROOT, bucket_name)
    if not os.path.isdir(bucket_path):
        os.makedirs(bucket_path)
    return bucket_path


def _ensure_settings_dir():
    if not os.path.isdir(SETTINGS_ROOT):
        os.makedirs(SETTINGS_ROOT)
    return SETTINGS_ROOT


def _cache_key(*parts):
    normalized = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _cache_file_path(bucket_name, cache_key_value, extension):
    return os.path.join(_ensure_cache_dir(bucket_name), "{}.{}".format(cache_key_value, extension))


def _read_cache_json(bucket_name, cache_key_value):
    cache_path = _cache_file_path(bucket_name, cache_key_value, "json")
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "r") as cache_file:
            return json.load(cache_file)
    except Exception:
        return None


def _write_cache_json(bucket_name, cache_key_value, data):
    cache_path = _cache_file_path(bucket_name, cache_key_value, "json")
    try:
        with open(cache_path, "w") as cache_file:
            json.dump(data, cache_file, indent=2, sort_keys=True)
    except Exception:
        pass


def _load_settings():
    if not os.path.isfile(SETTINGS_FILE_PATH):
        return {}
    try:
        with open(SETTINGS_FILE_PATH, "r") as settings_file:
            loaded = json.load(settings_file)
            return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _save_settings(settings_data):
    try:
        _ensure_settings_dir()
        with open(SETTINGS_FILE_PATH, "w") as settings_file:
            json.dump(settings_data or {}, settings_file, indent=2, sort_keys=True)
    except Exception:
        pass


def _get_project_address(doc):
    project_info = getattr(doc, "ProjectInformation", None)
    if project_info is None:
        return ""

    for param_name in ("Project Address", "Address", "Site Address"):
        try:
            param = project_info.LookupParameter(param_name)
            if param:
                value = param.AsString()
                if value and value.strip():
                    return value.strip()
        except Exception:
            pass
    return ""


def _get_project_site_location(doc):
    try:
        site = doc.SiteLocation
    except Exception:
        site = None
    if site is None:
        return None

    try:
        lat_deg = math.degrees(site.Latitude)
        lon_deg = math.degrees(site.Longitude)
    except Exception:
        return None

    if abs(lat_deg) < 1e-9 and abs(lon_deg) < 1e-9:
        return None
    if abs(lat_deg) > 90.0 or abs(lon_deg) > 180.0:
        return None
    return lat_deg, lon_deg


def _parse_radius(text):
    try:
        value = float((text or "").strip())
    except Exception:
        return None
    if value < MIN_RADIUS_M or value > MAX_RADIUS_M:
        return None
    return value


def _parse_dense_area(text):
    try:
        value = float((text or "").strip())
    except Exception:
        return None
    if value < MIN_DENSE_AREA_M:
        return None
    return value


def _normalize_dense_area_m(value):
    try:
        value = float(value)
    except Exception:
        return 0.0
    return max(0.0, value)


def _reverse_geocode(lat, lon):
    cache_key_value = _cache_key("reverse_geocode", round(float(lat), 6), round(float(lon), 6))
    cached = _read_cache_json("reverse_geocode", cache_key_value)
    if cached and cached.get("display_name"):
        return cached["display_name"]

    url = (
        "https://nominatim.openstreetmap.org/reverse?"
        "format=jsonv2&zoom=18&addressdetails=1&lat={}&lon={}"
    ).format(lat, lon)
    data = _http_get_json(url, timeout_sec=20)
    display_name = (data or {}).get("display_name") or ""
    if display_name.strip():
        display_name = display_name.strip()
        _write_cache_json("reverse_geocode", cache_key_value, {"display_name": display_name})
        return display_name
    return "{:.6f}, {:.6f}".format(lat, lon)


def _build_map_html(center_lat=None, center_lon=None, radius_m=0.0, label="", zoom=16):
    has_location = center_lat is not None and center_lon is not None
    lat_text = "null" if not has_location else "{:.8f}".format(float(center_lat))
    lon_text = "null" if not has_location else "{:.8f}".format(float(center_lon))
    radius_text = "{:.2f}".format(max(0.0, float(radius_m or 0.0)))
    zoom_text = str(int(zoom if zoom is not None else (16 if has_location else 2)))
    label_text = json.dumps(label or "")
    min_lat = min_lon = max_lat = max_lon = None
    if has_location and radius_m and radius_m > 0:
        min_lat, min_lon, max_lat, max_lon = _calculate_square_bounds(float(center_lat), float(center_lon), float(radius_m))
    min_lat_text = "null" if min_lat is None else "{:.8f}".format(min_lat)
    min_lon_text = "null" if min_lon is None else "{:.8f}".format(min_lon)
    max_lat_text = "null" if max_lat is None else "{:.8f}".format(max_lat)
    max_lon_text = "null" if max_lon is None else "{:.8f}".format(max_lon)
    return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="X-UA-Compatible" content="IE=Edge">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
  <style>
    html, body {{ height: 100%; margin: 0; padding: 0; overflow: hidden; background: #f2f5fa; }}
    #map {{ height: 100%; width: 100%; }}
    .map-note {{
      position: absolute;
      left: 12px;
      right: 12px;
      bottom: 12px;
      z-index: 9999;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid #d7dce6;
      border-radius: 6px;
      padding: 8px 10px;
      color: #5e6b80;
      font-family: Segoe UI, sans-serif;
      font-size: 12px;
      box-shadow: 0 2px 8px rgba(29, 39, 56, 0.12);
    }}
  </style>
  <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
</head>
<body>
  <div id="map"></div>
  <div class="map-note">Click anywhere on the map to set the context center.</div>
  <script type="text/javascript">
    (function () {{
      var lat = {lat_text};
      var lon = {lon_text};
      var radius = {radius_text};
      var zoom = {zoom_text};
      var label = {label_text};
      var minLat = {min_lat_text};
      var minLon = {min_lon_text};
      var maxLat = {max_lat_text};
      var maxLon = {max_lon_text};

      var map = L.map('map', {{ zoomControl: true }});
      L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors'
      }}).addTo(map);

      if (lat === null || lon === null) {{
        map.setView([20.0, 0.0], 2);
      }} else {{
        map.setView([lat, lon], zoom);
        var marker = L.marker([lat, lon]).addTo(map);
        if (minLat !== null && minLon !== null && maxLat !== null && maxLon !== null) {{
          L.rectangle([[minLat, minLon], [maxLat, maxLon]], {{
            color: '#2D6CDF',
            weight: 2,
            fillColor: '#2D6CDF',
            fillOpacity: 0.12
          }}).addTo(map);
        }}
        if (label) {{
          marker.bindPopup(label).openPopup();
        }}
      }}

      map.on('click', function (evt) {{
        var clickLat = evt.latlng.lat.toFixed(6);
        var clickLon = evt.latlng.lng.toFixed(6);
        window.location.href = '{scheme}?lat=' + encodeURIComponent(clickLat) + '&lon=' + encodeURIComponent(clickLon);
      }});
    }})();
  </script>
</body>
</html>""".format(
        lat_text=lat_text,
        lon_text=lon_text,
        radius_text=radius_text,
        zoom_text=zoom_text,
        label_text=label_text,
        min_lat_text=min_lat_text,
        min_lon_text=min_lon_text,
        max_lat_text=max_lat_text,
        max_lon_text=max_lon_text,
        scheme=MAP_CLICK_SCHEME,
    )


def _render_map(browser, center_lat=None, center_lon=None, radius_m=0.0, label="", zoom=16):
    if browser is None:
        return
    browser.NavigateToString(_build_map_html(center_lat, center_lon, radius_m, label, zoom))


def _http_get_text(url, timeout_sec=20):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    response = urlopen(request, timeout=timeout_sec)
    try:
        return decode_to_text(response.read(), "utf-8")
    finally:
        response.close()


def _meters_per_degree(center_lat):
    meters_per_degree_lat = 111320.0
    meters_per_degree_lon = max(1e-9, meters_per_degree_lat * math.cos(math.radians(center_lat)))
    return meters_per_degree_lat, meters_per_degree_lon


def _sample_uk_terrain_elevation(lat, lon, endpoint, layer_name, sample_window_m=TERRAIN_SAMPLE_WINDOW_M):
    point_cache_key = _cache_key(
        "uk_terrain_point",
        endpoint,
        layer_name,
        round(float(lat), 8),
        round(float(lon), 8),
        round(float(sample_window_m), 3),
    )
    cached_value = _read_cache_json("terrain_point", point_cache_key)
    if cached_value and "elevation_m" in cached_value:
        return cached_value.get("elevation_m")

    meters_per_degree_lat, meters_per_degree_lon = _meters_per_degree(lat)
    delta_lat = sample_window_m / meters_per_degree_lat
    delta_lon = sample_window_m / meters_per_degree_lon
    query = urllib_parse.urlencode(
        {
            "service": "WMS",
            "version": "1.1.1",
            "request": "GetFeatureInfo",
            "layers": layer_name,
            "query_layers": layer_name,
            "styles": "",
            "srs": "EPSG:4326",
            "bbox": "{:.8f},{:.8f},{:.8f},{:.8f}".format(lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat),
            "width": 3,
            "height": 3,
            "x": 1,
            "y": 1,
            "format": "image/png",
            "info_format": "text/plain",
        }
    )
    text = _http_get_text("{}?{}".format(endpoint, query), timeout_sec=30)
    try:
        root = ET.fromstring(text)
        for element in root.iter():
            if element.tag.endswith("band-0-pixel-value") and element.text:
                value = float(element.text.strip())
                _write_cache_json("terrain_point", point_cache_key, {"elevation_m": value})
                return value
    except Exception:
        pass

    match = re.search(r"Elevation\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)", text or "")
    if match:
        value = float(match.group(1))
        _write_cache_json("terrain_point", point_cache_key, {"elevation_m": value})
        return value
    return None


def _sample_uk_dtm_elevation(lat, lon, sample_window_m=TERRAIN_SAMPLE_WINDOW_M):
    return _sample_uk_terrain_elevation(lat, lon, UK_TERRAIN_DTM_WMS_ENDPOINT, UK_TERRAIN_DTM_WMS_LAYER, sample_window_m)


def _sample_uk_dsm_elevation(lat, lon, sample_window_m=TERRAIN_SAMPLE_WINDOW_M):
    return _sample_uk_terrain_elevation(lat, lon, UK_TERRAIN_DSM_WMS_ENDPOINT, UK_TERRAIN_DSM_WMS_LAYER, sample_window_m)


def _build_terrain_grid(center_lat, center_lon, radius_m, dense_square_size_m, fast_mode=False):
    dense_square_size_m = _normalize_dense_area_m(dense_square_size_m)
    if fast_mode:
        dense_square_size_m = 0.0
    grid_cache_key = _cache_key(
        "terrain_grid",
        round(float(center_lat), 8),
        round(float(center_lon), 8),
        round(float(radius_m), 3),
        round(float(dense_square_size_m), 3),
        bool(fast_mode),
        FAST_TERRAIN_MIN_GRID_SPACING_M if fast_mode else TERRAIN_MIN_GRID_SPACING_M,
        TERRAIN_DENSE_GRID_SPACING_M,
        FAST_MAX_TERRAIN_SAMPLE_POINTS if fast_mode else MAX_TERRAIN_SAMPLE_POINTS,
        FAST_BASE_TOTAL_TERRAIN_SAMPLE_POINTS if fast_mode else BASE_TOTAL_TERRAIN_SAMPLE_POINTS,
        FAST_MAX_TOTAL_TERRAIN_SAMPLE_POINTS if fast_mode else MAX_TOTAL_TERRAIN_SAMPLE_POINTS,
        MAX_DENSE_GRID_SAMPLE_POINTS,
    )
    cached_grid = _read_cache_json("terrain_grid", grid_cache_key)
    if cached_grid:
        return cached_grid

    def _estimate_grid_point_count(half_size_m, target_spacing_m):
        steps = max(2, int(math.ceil(half_size_m / max(1.0, target_spacing_m))))
        return ((steps * 2) + 1) ** 2

    def _sample_regular_grid(half_size_m, target_spacing_m):
        steps = max(2, int(math.ceil(half_size_m / max(1.0, target_spacing_m))))
        per_side_local = (steps * 2) + 1
        step_lat_local = (half_size_m / float(steps)) / _meters_per_degree(center_lat)[0]
        step_lon_local = (half_size_m / float(steps)) / _meters_per_degree(center_lat)[1]
        rows_local = []
        points_local = []
        for row_index in range(per_side_local):
            lat = center_lat + ((steps - row_index) * step_lat_local)
            row_values = []
            for col_index in range(per_side_local):
                lon = center_lon + ((col_index - steps) * step_lon_local)
                elevation = _sample_uk_dtm_elevation(lat, lon)
                row_values.append(elevation)
                if elevation is not None:
                    points_local.append(
                        {
                            "lat": lat,
                            "lon": lon,
                            "elevation_m": elevation,
                            "row": row_index,
                            "col": col_index,
                        }
                    )
            rows_local.append(row_values)
        return {
            "rows": rows_local,
            "points": points_local,
            "per_side": per_side_local,
            "steps_each_side": steps,
            "step_lat": step_lat_local,
            "step_lon": step_lon_local,
            "half_size_m": half_size_m,
        }

    def _reduce_grid_points(points_local, target_count):
        if target_count <= 0:
            return []
        if len(points_local) <= target_count:
            return list(points_local)

        stride = 2
        reduced = list(points_local)
        while stride <= 32:
            reduced = [
                point
                for point in points_local
                if (point.get("row", 0) % stride == 0) and (point.get("col", 0) % stride == 0)
            ]
            if len(reduced) <= target_count:
                break
            stride += 1

        if len(reduced) > target_count:
            step = max(1, int(math.ceil(len(reduced) / float(target_count))))
            reduced = reduced[::step]

        return reduced[:target_count]

    max_terrain_sample_points = FAST_MAX_TERRAIN_SAMPLE_POINTS if fast_mode else MAX_TERRAIN_SAMPLE_POINTS
    base_total_terrain_sample_points = FAST_BASE_TOTAL_TERRAIN_SAMPLE_POINTS if fast_mode else BASE_TOTAL_TERRAIN_SAMPLE_POINTS
    max_total_terrain_sample_points = FAST_MAX_TOTAL_TERRAIN_SAMPLE_POINTS if fast_mode else MAX_TOTAL_TERRAIN_SAMPLE_POINTS

    spacing_m = max(FAST_TERRAIN_MIN_GRID_SPACING_M, radius_m / 3.0) if fast_mode else max(TERRAIN_MIN_GRID_SPACING_M, radius_m / 4.0)
    coarse_steps = max(2, int(math.ceil(radius_m / spacing_m)))
    while ((coarse_steps * 2) + 1) ** 2 > max_terrain_sample_points and coarse_steps > 2:
        coarse_steps -= 1
    coarse_spacing_m = radius_m / float(coarse_steps)
    coarse_grid = _sample_regular_grid(radius_m, coarse_spacing_m)

    dense_half_size_m = 0.0
    dense_grid = None
    dense_spacing_m = TERRAIN_DENSE_GRID_SPACING_M
    if dense_square_size_m >= MIN_DENSE_AREA_M:
        dense_half_size_m = min(radius_m, dense_square_size_m / 2.0)
    if dense_half_size_m >= TERRAIN_DENSE_GRID_SPACING_M:
        while _estimate_grid_point_count(dense_half_size_m, dense_spacing_m) > MAX_DENSE_GRID_SAMPLE_POINTS:
            dense_spacing_m += TERRAIN_DENSE_GRID_SPACING_M
        dense_grid = _sample_regular_grid(dense_half_size_m, dense_spacing_m)

    coarse_points = list(coarse_grid.get("points") or [])
    dense_points = []
    seen_keys = {(round(point["lat"], 8), round(point["lon"], 8)) for point in coarse_points}
    for point in dense_grid.get("points") if dense_grid is not None else []:
        point_key = (round(point["lat"], 8), round(point["lon"], 8))
        if point_key not in seen_keys:
            dense_points.append(point)

    target_total_points = max(base_total_terrain_sample_points, len(coarse_points))
    if dense_points:
        target_total_points = min(
            max_total_terrain_sample_points,
            max(target_total_points, len(coarse_points) + len(dense_points)),
        )
    remaining_slots = max(0, target_total_points - len(coarse_points))
    dense_points = _reduce_grid_points(dense_points, remaining_slots)
    merged_points = coarse_points + dense_points

    min_elevation = None
    max_elevation = None
    for point in merged_points:
        elevation = point["elevation_m"]
        min_elevation = elevation if min_elevation is None else min(min_elevation, elevation)
        max_elevation = elevation if max_elevation is None else max(max_elevation, elevation)

    if len(merged_points) < 9 or min_elevation is None or max_elevation is None:
        raise Exception("UK DTM terrain sampling returned too few valid elevation points.")

    terrain_grid = {
        "rows": coarse_grid["rows"],
        "points": merged_points,
        "per_side": coarse_grid["per_side"],
        "steps_each_side": coarse_grid["steps_each_side"],
        "step_lat": coarse_grid["step_lat"],
        "step_lon": coarse_grid["step_lon"],
        "dense_rows": dense_grid["rows"] if dense_grid is not None else None,
        "dense_per_side": dense_grid["per_side"] if dense_grid is not None else None,
        "dense_steps_each_side": dense_grid["steps_each_side"] if dense_grid is not None else None,
        "dense_step_lat": dense_grid["step_lat"] if dense_grid is not None else None,
        "dense_step_lon": dense_grid["step_lon"] if dense_grid is not None else None,
        "dense_half_size_m": dense_half_size_m if dense_grid is not None else 0.0,
        "min_elevation_m": min_elevation,
        "max_elevation_m": max_elevation,
    }
    _write_cache_json("terrain_grid", grid_cache_key, terrain_grid)
    return terrain_grid


def _terrain_nearest_elevation(terrain_grid, lat, lon):
    nearest = None
    nearest_dist = None
    for point in terrain_grid.get("points") or []:
        dist = math.hypot(point["lat"] - lat, point["lon"] - lon)
        if nearest_dist is None or dist < nearest_dist:
            nearest = point["elevation_m"]
            nearest_dist = dist
    return nearest


def _terrain_bilinear_elevation(terrain_grid, center_lat, center_lon, lat, lon):
    dense_half_size_m = terrain_grid.get("dense_half_size_m") or 0.0
    grid_rows = terrain_grid["rows"]
    step_lat = terrain_grid["step_lat"]
    step_lon = terrain_grid["step_lon"]
    steps_each_side = terrain_grid["steps_each_side"]
    per_side = terrain_grid["per_side"]

    if dense_half_size_m > 0.0:
        dx_m, dy_m = _project_latlon(lat, lon, center_lat, center_lon)
        if abs(dx_m) <= dense_half_size_m and abs(dy_m) <= dense_half_size_m:
            dense_rows = terrain_grid.get("dense_rows")
            if dense_rows:
                grid_rows = dense_rows
                step_lat = terrain_grid["dense_step_lat"]
                step_lon = terrain_grid["dense_step_lon"]
                steps_each_side = terrain_grid["dense_steps_each_side"]
                per_side = terrain_grid["dense_per_side"]

    max_index = per_side - 1
    row_position = ((center_lat + (steps_each_side * step_lat)) - lat) / step_lat
    col_position = (lon - (center_lon - (steps_each_side * step_lon))) / step_lon

    row0 = int(math.floor(row_position))
    col0 = int(math.floor(col_position))
    row1 = min(max_index, row0 + 1)
    col1 = min(max_index, col0 + 1)
    row0 = max(0, min(max_index, row0))
    col0 = max(0, min(max_index, col0))

    q11 = grid_rows[row0][col0]
    q12 = grid_rows[row0][col1]
    q21 = grid_rows[row1][col0]
    q22 = grid_rows[row1][col1]

    if None in (q11, q12, q21, q22):
        return _terrain_nearest_elevation(terrain_grid, lat, lon)

    tx = 0.0 if col1 == col0 else max(0.0, min(1.0, col_position - col0))
    ty = 0.0 if row1 == row0 else max(0.0, min(1.0, row_position - row0))
    return (
        (q11 * (1.0 - tx) * (1.0 - ty))
        + (q12 * tx * (1.0 - ty))
        + (q21 * (1.0 - tx) * ty)
        + (q22 * tx * ty)
    )


def _latlon_ring_centroid(ring):
    if not ring:
        return None

    points = ring[:-1] if len(ring) > 1 and _point_key(ring[0]) == _point_key(ring[-1]) else ring
    if len(points) < 3:
        return points[0] if points else None

    area = 0.0
    centroid_lat = 0.0
    centroid_lon = 0.0
    for index in range(len(points)):
        lat1, lon1 = points[index]
        lat2, lon2 = points[(index + 1) % len(points)]
        cross = (lon1 * lat2) - (lon2 * lat1)
        area += cross
        centroid_lon += (lon1 + lon2) * cross
        centroid_lat += (lat1 + lat2) * cross

    if abs(area) <= 1e-9:
        return (
            sum(point[0] for point in points) / float(len(points)),
            sum(point[1] for point in points) / float(len(points)),
        )

    area *= 0.5
    return centroid_lat / (6.0 * area), centroid_lon / (6.0 * area)


def _show_dialog(doc):
    xaml_path = os.path.join(script_dir, "UKContextBuilderDialog.xaml")
    if not os.path.isfile(xaml_path):
        raise Exception("Missing dialog XAML: {}".format(xaml_path))

    xaml_text = File.ReadAllText(xaml_path)
    xaml_reader = XmlReader.Create(StringReader(xaml_text))
    window = XamlReader.Load(xaml_reader)
    apply_window_title(window, TITLE)
    _set_owner(window)

    address_text = window.FindName("AddressTextBox")
    radius_text = window.FindName("RadiusTextBox")
    validation_text = window.FindName("ValidationText")
    dryrun_button = window.FindName("DryRunButton")
    run_button = window.FindName("RunButton")
    cancel_button = window.FindName("CancelButton")
    locate_button = window.FindName("LocateButton")
    logo_image = window.FindName("LogoImage")
    map_browser = window.FindName("MapBrowser")
    map_hint_text = window.FindName("MapHintText")
    dense_area_text = window.FindName("DenseAreaTextBox")
    use_dense_area_checkbox = window.FindName("UseDenseAreaCheckBox")
    fast_mode_checkbox = window.FindName("FastModeCheckBox")
    buildings_checkbox = window.FindName("BuildingsCheckBox")
    roads_checkbox = window.FindName("RoadsCheckBox")
    tracks_checkbox = window.FindName("TracksCheckBox")
    parcels_checkbox = window.FindName("ParcelsCheckBox")
    parks_checkbox = window.FindName("ParksCheckBox")
    water_checkbox = window.FindName("WaterCheckBox")
    terrain_checkbox = window.FindName("TerrainCheckBox")

    _load_logo(logo_image)

    saved_settings = _load_settings()
    initial_address = (saved_settings.get("address") or "").strip() or (_get_project_address(doc) or "")
    initial_site_location = _get_project_site_location(doc)
    if address_text is not None:
        address_text.Text = initial_address
    if radius_text is not None:
        radius_text.Text = str(int(saved_settings.get("radius_m") or DEFAULT_RADIUS_M))
    if dense_area_text is not None:
        dense_area_text.Text = str(int(saved_settings.get("dense_area_m") or TERRAIN_DENSE_SQUARE_SIZE_M))
    if use_dense_area_checkbox is not None and "use_dense_area" in saved_settings:
        use_dense_area_checkbox.IsChecked = bool(saved_settings.get("use_dense_area"))
    if fast_mode_checkbox is not None and "fast_mode" in saved_settings:
        fast_mode_checkbox.IsChecked = bool(saved_settings.get("fast_mode"))
    if buildings_checkbox is not None and "buildings" in saved_settings:
        buildings_checkbox.IsChecked = bool(saved_settings.get("buildings"))
    if roads_checkbox is not None and "roads" in saved_settings:
        roads_checkbox.IsChecked = bool(saved_settings.get("roads"))
    if tracks_checkbox is not None and "tracks" in saved_settings:
        tracks_checkbox.IsChecked = bool(saved_settings.get("tracks"))
    if parcels_checkbox is not None and "parcels" in saved_settings:
        parcels_checkbox.IsChecked = bool(saved_settings.get("parcels"))
    if parks_checkbox is not None and "parks" in saved_settings:
        parks_checkbox.IsChecked = bool(saved_settings.get("parks"))
    if water_checkbox is not None and "water" in saved_settings:
        water_checkbox.IsChecked = bool(saved_settings.get("water"))
    if terrain_checkbox is not None and "terrain" in saved_settings:
        terrain_checkbox.IsChecked = bool(saved_settings.get("terrain"))
    result = {"ok": False}
    map_state = {"location": None, "label": ""}

    def _set_validation(message):
        if message:
            validation_text.Text = message
            validation_text.Visibility = Visibility.Visible
        else:
            validation_text.Text = ""
            validation_text.Visibility = Visibility.Collapsed

    def _set_map_hint(message):
        if map_hint_text is not None:
            map_hint_text.Text = message or "Click map to choose the import center."

    def _get_valid_radius_or_default():
        parsed_radius = _parse_radius(radius_text.Text if radius_text is not None else "")
        if parsed_radius is None:
            return DEFAULT_RADIUS_M
        return parsed_radius

    def _update_dense_area_state(*args):
        fast_mode_enabled = bool(fast_mode_checkbox.IsChecked) if fast_mode_checkbox is not None else False
        if use_dense_area_checkbox is not None:
            use_dense_area_checkbox.IsEnabled = not fast_mode_enabled
            use_dense_area_checkbox.Opacity = 1.0 if not fast_mode_enabled else 0.55
        enabled = (bool(use_dense_area_checkbox.IsChecked) if use_dense_area_checkbox is not None else True) and not fast_mode_enabled
        if dense_area_text is not None:
            dense_area_text.IsEnabled = enabled
            dense_area_text.Opacity = 1.0 if enabled else 0.55

    def _set_map_location(lat, lon, label, zoom=16, update_address=False):
        map_state["location"] = (float(lat), float(lon))
        map_state["label"] = label or ""
        if update_address and address_text is not None:
            address_text.Text = label or "{:.6f}, {:.6f}".format(float(lat), float(lon))
        _render_map(map_browser, float(lat), float(lon), _get_valid_radius_or_default(), label or "", zoom)
        _set_map_hint("Map centered at {:.6f}, {:.6f}".format(float(lat), float(lon)))

    def _show_default_map():
        _render_map(map_browser, None, None, 0.0, "", 2)
        _set_map_hint("Click map to choose the import center.")

    def _locate_from_address_or_site():
        address_value = (address_text.Text or "").strip() if address_text is not None else ""
        if address_value:
            lat, lon = _geocode_address(address_value)
            _set_map_location(lat, lon, address_value, zoom=16, update_address=False)
            return

        if initial_site_location is None:
            raise Exception("Enter an address or set the project site location in Revit.")

        lat, lon = initial_site_location
        _set_map_location(
            lat,
            lon,
            "Project site location ({:.6f}, {:.6f})".format(lat, lon),
            zoom=15,
            update_address=False,
        )

    def _on_map_navigating(sender, args):
        try:
            uri = args.Uri
            absolute_uri = uri.AbsoluteUri if uri is not None else ""
            if not absolute_uri.lower().startswith(MAP_CLICK_SCHEME):
                return

            args.Cancel = True
            parsed = urllib_parse.urlparse(absolute_uri)
            query = urllib_parse.parse_qs(parsed.query)
            lat = float((query.get("lat") or [None])[0])
            lon = float((query.get("lon") or [None])[0])
            label = _reverse_geocode(lat, lon)
            _set_map_location(lat, lon, label, zoom=17, update_address=True)
            _set_validation("")
        except Exception as ex:
            try:
                args.Cancel = True
            except Exception:
                pass
            _set_validation("Map click failed: {}".format(str(ex)))

    def _on_locate(sender, args):
        try:
            _locate_from_address_or_site()
            _set_validation("")
        except Exception as ex:
            _set_validation("Unable to locate address: {}".format(str(ex)))

    def _on_run(sender, args):
        _commit_dialog_result("run")

    def _commit_dialog_result(mode):
        address = (address_text.Text or "").strip()
        radius = _parse_radius(radius_text.Text)
        fast_mode = bool(fast_mode_checkbox.IsChecked) if fast_mode_checkbox is not None else False
        use_dense_area = bool(use_dense_area_checkbox.IsChecked) if use_dense_area_checkbox is not None else True
        dense_area_m = 0.0
        if use_dense_area and not fast_mode:
            dense_area_m = _parse_dense_area(dense_area_text.Text if dense_area_text is not None else "")
        layers = {
            "buildings": bool(buildings_checkbox.IsChecked) if buildings_checkbox is not None else True,
            "roads": bool(roads_checkbox.IsChecked) if roads_checkbox is not None else True,
            "tracks": bool(tracks_checkbox.IsChecked) if tracks_checkbox is not None else True,
            "parcels": bool(parcels_checkbox.IsChecked) if parcels_checkbox is not None else False,
            "parks": bool(parks_checkbox.IsChecked) if parks_checkbox is not None else True,
            "water": bool(water_checkbox.IsChecked) if water_checkbox is not None else True,
            "terrain": bool(terrain_checkbox.IsChecked) if terrain_checkbox is not None else False,
        }
        if radius is None:
            _set_validation("Radius must be between {} and {} meters.".format(int(MIN_RADIUS_M), int(MAX_RADIUS_M)))
            return
        if use_dense_area and (not fast_mode) and dense_area_m is None:
            _set_validation("Dense area must be at least {} meters.".format(int(MIN_DENSE_AREA_M)))
            return
        if not any(layers.values()):
            _set_validation("Select at least one layer to import.")
            return
        if not address and _get_project_site_location(doc) is None:
            _set_validation("Enter an address or set the project site location in Revit.")
            return
        result["ok"] = True
        result["mode"] = mode
        result["address"] = address
        result["radius_m"] = radius
        result["dense_area_m"] = dense_area_m
        result["fast_mode"] = fast_mode
        result["layers"] = layers
        result["location"] = map_state.get("location")
        result["location_label"] = map_state.get("label") or ""
        _save_settings(
            {
                "address": address,
                "radius_m": radius,
                "dense_area_m": dense_area_m,
                "fast_mode": fast_mode,
                "use_dense_area": use_dense_area,
                "buildings": layers.get("buildings"),
                "roads": layers.get("roads"),
                "tracks": layers.get("tracks"),
                "parcels": layers.get("parcels"),
                "parks": layers.get("parks"),
                "water": layers.get("water"),
                "terrain": layers.get("terrain"),
            }
        )
        window.DialogResult = True
        window.Close()

    def _on_dryrun(sender, args):
        _commit_dialog_result("dryrun")

    def _on_cancel(sender, args):
        window.DialogResult = False
        window.Close()

    if dryrun_button is not None:
        dryrun_button.Click += _on_dryrun
    run_button.Click += _on_run
    cancel_button.Click += _on_cancel
    if locate_button is not None:
        locate_button.Click += _on_locate
    if use_dense_area_checkbox is not None:
        use_dense_area_checkbox.Checked += _update_dense_area_state
        use_dense_area_checkbox.Unchecked += _update_dense_area_state
    if fast_mode_checkbox is not None:
        fast_mode_checkbox.Checked += _update_dense_area_state
        fast_mode_checkbox.Unchecked += _update_dense_area_state
    if map_browser is not None:
        map_browser.Navigating += _on_map_navigating

    _update_dense_area_state()

    try:
        if initial_address:
            _locate_from_address_or_site()
        elif initial_site_location is not None:
            lat, lon = initial_site_location
            _set_map_location(
                lat,
                lon,
                "Project site location ({:.6f}, {:.6f})".format(lat, lon),
                zoom=15,
                update_address=False,
            )
        else:
            _show_default_map()
    except Exception:
        _show_default_map()

    if window.ShowDialog() != True:
        return None
    return result if result.get("ok") else None


def _http_get_json(url, timeout_sec=20):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    response = urlopen(request, timeout=timeout_sec)
    try:
        return json.loads(decode_to_text(response.read(), "utf-8"))
    finally:
        response.close()


def _http_post_text(url, body_text, timeout_sec=20):
    payload = urllib_parse.urlencode({"data": body_text}).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
    )
    response = urlopen(request, timeout=timeout_sec)
    try:
        return decode_to_text(response.read(), "utf-8")
    finally:
        response.close()


def _geocode_address(address):
    normalized_address = (address or "").strip()
    cache_key_value = _cache_key("geocode_address", normalized_address.lower())
    cached = _read_cache_json("geocode_address", cache_key_value)
    if cached and ("lat" in cached) and ("lon" in cached):
        return float(cached["lat"]), float(cached["lon"])

    query = urllib_parse.quote(address)
    url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=gb&q={}".format(query)
    data = _http_get_json(url, timeout_sec=20)
    if not data:
        raise Exception("Address not found.")
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    _write_cache_json("geocode_address", cache_key_value, {"lat": lat, "lon": lon, "address": normalized_address})
    return lat, lon


def _calculate_square_bounds(center_lat, center_lon, radius_m):
    lat_rad = math.radians(center_lat)
    meters_per_degree_lat = 111320.0
    meters_per_degree_lon = max(1e-9, meters_per_degree_lat * math.cos(lat_rad))
    delta_lat = radius_m / meters_per_degree_lat
    delta_lon = radius_m / meters_per_degree_lon
    return (
        center_lat - delta_lat,
        center_lon - delta_lon,
        center_lat + delta_lat,
        center_lon + delta_lon,
    )


def _build_overpass_query(center_lat, center_lon, radius_m):
    min_lat, min_lon, max_lat, max_lon = _calculate_square_bounds(center_lat, center_lon, radius_m)
    return """
[out:json][timeout:90];
(
  way({south},{west},{north},{east})["building"];
  relation({south},{west},{north},{east})["building"];
  way({south},{west},{north},{east})["building:part"];
  relation({south},{west},{north},{east})["building:part"];
  way({south},{west},{north},{east})["highway"];
  way({south},{west},{north},{east})["leisure"="park"];
  relation({south},{west},{north},{east})["leisure"="park"];
  way({south},{west},{north},{east})["landuse"~"grass|recreation_ground|village_green|greenfield"];
  relation({south},{west},{north},{east})["landuse"~"grass|recreation_ground|village_green|greenfield"];
  way({south},{west},{north},{east})["natural"="water"];
  relation({south},{west},{north},{east})["natural"="water"];
  way({south},{west},{north},{east})["water"];
  relation({south},{west},{north},{east})["water"];
  way({south},{west},{north},{east})["landuse"~"reservoir|basin"];
  relation({south},{west},{north},{east})["landuse"~"reservoir|basin"];
  way({south},{west},{north},{east})["waterway"];
  way({south},{west},{north},{east})["railway"~"rail|light_rail|tram|subway|narrow_gauge"];
  way({south},{west},{north},{east})["boundary"="cadastral"];
  relation({south},{west},{north},{east})["boundary"="cadastral"];
  way({south},{west},{north},{east})["parcel"];
  relation({south},{west},{north},{east})["parcel"];
);
out body geom;
""".strip().format(
        south=min_lat,
        west=min_lon,
        north=max_lat,
        east=max_lon,
    )


def _fetch_overpass_json(query):
    cache_key_value = _cache_key("overpass_query", query)
    cached = _read_cache_json("overpass_query", cache_key_value)
    if cached:
        cached["_endpoint"] = cached.get("_endpoint") or "<cache>"
        cached["_cache_source"] = "appdata"
        return cached

    failures = []
    empty_data = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = json.loads(_http_post_text(endpoint, query, timeout_sec=45))
            if data.get("elements"):
                data["_endpoint"] = endpoint
                _write_cache_json("overpass_query", cache_key_value, data)
                return data
            if empty_data is None:
                data["_endpoint"] = endpoint
                empty_data = data
        except Exception as ex:
            failures.append("{} | {}".format(endpoint, str(ex)))

    if empty_data is not None:
        _write_cache_json("overpass_query", cache_key_value, empty_data)
        return empty_data

    raise Exception("All Overpass endpoints failed.\n{}".format("\n".join(failures[:MAX_FAILURES_IN_REPORT])))


def _point_key(point):
    return (round(point[0], 8), round(point[1], 8))


def _geometry_to_points(geometry):
    points = []
    for item in geometry or []:
        try:
            points.append((float(item["lat"]), float(item["lon"])))
        except Exception:
            continue
    return points


def _close_ring(points):
    clean = [point for point in (points or []) if point is not None]
    if len(clean) < 3:
        return clean
    if _point_key(clean[0]) != _point_key(clean[-1]):
        clean.append(clean[0])
    return clean


def _assemble_geometry_rings(members, role_name):
    segments = []
    for member in members or []:
        if (member.get("type") or "").lower() != "way":
            continue

        role = (member.get("role") or "").strip().lower()
        if role_name == "inner":
            if role != "inner":
                continue
        elif role == "inner":
            continue

        points = _geometry_to_points(member.get("geometry"))
        if len(points) < 2:
            continue
        segments.append(points)

    rings = []
    while segments:
        chain = list(segments.pop(0))
        progressed = True
        while progressed and len(chain) >= 2 and _point_key(chain[0]) != _point_key(chain[-1]):
            progressed = False
            for index, candidate in enumerate(segments):
                candidate_start = _point_key(candidate[0])
                candidate_end = _point_key(candidate[-1])
                chain_start = _point_key(chain[0])
                chain_end = _point_key(chain[-1])

                if chain_end == candidate_start:
                    chain.extend(candidate[1:])
                elif chain_end == candidate_end:
                    chain.extend(list(reversed(candidate[:-1])))
                elif chain_start == candidate_end:
                    chain = candidate[:-1] + chain
                elif chain_start == candidate_start:
                    chain = list(reversed(candidate[1:])) + chain
                else:
                    continue

                segments.pop(index)
                progressed = True
                break

        chain = _close_ring(chain)
        unique_points = {_point_key(point) for point in chain[:-1]} if len(chain) > 1 else set()
        if len(chain) >= 4 and len(unique_points) >= 3:
            rings.append(chain)

    return rings


def _point_in_ring_latlon(point, ring):
    x = point[1]
    y = point[0]
    inside = False
    for index in range(len(ring) - 1):
        x1 = ring[index][1]
        y1 = ring[index][0]
        x2 = ring[index + 1][1]
        y2 = ring[index + 1][0]
        intersects = (y1 > y) != (y2 > y)
        if not intersects:
            continue
        slope_x = ((x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12)) + x1
        if x < slope_x:
            inside = not inside
    return inside


def _is_building_tag(tags):
    for key in ("building", "building:part"):
        value = (tags.get(key) or "").strip().lower()
        if value and value not in ("no", "false", "0"):
            return True
    return False


def _is_park_tag(tags):
    leisure = (tags.get("leisure") or "").strip().lower()
    landuse = (tags.get("landuse") or "").strip().lower()
    natural = (tags.get("natural") or "").strip().lower()
    return (
        leisure in ("park", "garden", "playground", "recreation_ground")
        or landuse in ("grass", "recreation_ground", "village_green", "greenfield")
        or natural == "grassland"
    )


def _is_water_tag(tags):
    natural = (tags.get("natural") or "").strip().lower()
    landuse = (tags.get("landuse") or "").strip().lower()
    water = (tags.get("water") or "").strip().lower()
    waterway = (tags.get("waterway") or "").strip().lower()
    return (
        natural == "water"
        or landuse in ("reservoir", "basin")
        or bool(water)
        or waterway == "riverbank"
    )


def _is_track_tag(tags):
    railway = (tags.get("railway") or "").strip().lower()
    return railway in ("rail", "light_rail", "tram", "subway", "narrow_gauge")


def _is_parcel_tag(tags):
    boundary = (tags.get("boundary") or "").strip().lower()
    parcel = (tags.get("parcel") or "").strip().lower()
    return boundary == "cadastral" or bool(parcel)


def _collect_elements(elements):
    ways = {}
    relations = []
    roads = []
    tracks = []
    waterways = []

    for element in elements or []:
        element_type = (element.get("type") or "").lower()
        element_id = str(element.get("id"))
        tags = element.get("tags") or {}

        if element_type == "way":
            ways[element_id] = element

            highway = (tags.get("highway") or "").strip().lower()
            if highway and highway not in PEDESTRIAN_HIGHWAYS:
                roads.append(element)

            if _is_track_tag(tags):
                tracks.append(element)

            waterway = (tags.get("waterway") or "").strip().lower()
            if waterway and waterway not in ("dam", "weir", "dock"):
                waterways.append(element)
        elif element_type == "relation":
            relations.append(element)

    return ways, relations, roads, tracks, waterways


def _build_area_features(ways, relations, predicate):
    features = []
    relation_way_ids = set()

    for relation in relations or []:
        tags = relation.get("tags") or {}
        if not predicate(tags):
            continue

        members = relation.get("members") or []
        outer_rings = _assemble_geometry_rings(members, "outer")
        if not outer_rings:
            continue

        inner_rings = _assemble_geometry_rings(members, "inner")
        for member in members:
            if (member.get("type") or "").lower() == "way" and member.get("ref") is not None:
                relation_way_ids.add(str(member.get("ref")))

        for outer_index, outer_ring in enumerate(outer_rings):
            assigned_inners = []
            for inner_ring in inner_rings:
                if _point_in_ring_latlon(inner_ring[0], outer_ring):
                    assigned_inners.append(inner_ring)

            features.append(
                {
                    "id": "relation/{}:{}".format(relation.get("id"), outer_index + 1),
                    "tags": tags,
                    "outer": outer_ring,
                    "inners": assigned_inners,
                }
            )

    for way_id, way in (ways or {}).items():
        if way_id in relation_way_ids:
            continue

        tags = way.get("tags") or {}
        if not predicate(tags):
            continue

        ring = _close_ring(_geometry_to_points(way.get("geometry")))
        unique_points = {_point_key(point) for point in ring[:-1]} if len(ring) > 1 else set()
        if len(ring) < 4 or len(unique_points) < 3:
            continue

        features.append(
            {
                "id": "way/{}".format(way_id),
                "tags": tags,
                "outer": ring,
                "inners": [],
            }
        )

    return features


def _project_latlon(lat, lon, origin_lat, origin_lon):
    earth_radius_m = 6378137.0
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    origin_lat_rad = math.radians(origin_lat)
    origin_lon_rad = math.radians(origin_lon)
    x_m = (lon_rad - origin_lon_rad) * math.cos(origin_lat_rad) * earth_radius_m
    y_m = (lat_rad - origin_lat_rad) * earth_radius_m
    return x_m, y_m


def _project_ring(ring, origin_lat, origin_lon):
    return [_project_latlon(point[0], point[1], origin_lat, origin_lon) for point in ring or []]


def _remove_duplicate_xy(points, tolerance_m=0.05):
    cleaned = []
    for point in points or []:
        if not cleaned:
            cleaned.append(point)
            continue

        prev_x, prev_y = cleaned[-1]
        curr_x, curr_y = point
        if math.hypot(curr_x - prev_x, curr_y - prev_y) > tolerance_m:
            cleaned.append(point)

    if len(cleaned) > 1:
        first_x, first_y = cleaned[0]
        last_x, last_y = cleaned[-1]
        if math.hypot(first_x - last_x, first_y - last_y) <= tolerance_m:
            cleaned[-1] = cleaned[0]

    return cleaned


def _signed_area_xy(points):
    area = 0.0
    for index in range(len(points) - 1):
        x1, y1 = points[index]
        x2, y2 = points[index + 1]
        area += (x1 * y2) - (x2 * y1)
    return area / 2.0


def _orient_ring(points, clockwise):
    area = _signed_area_xy(points)
    is_clockwise = area < 0
    if is_clockwise != clockwise:
        return list(reversed(points))
    return points


def _parse_numeric(raw_text):
    text = (raw_text or "").strip().replace(",", ".")
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _parse_height_m(tags):
    for key in ("height", "building:height", "est_height"):
        raw_value = tags.get(key)
        if not raw_value:
            continue

        value = _parse_numeric(raw_value)
        if value is None or value <= 0:
            continue

        text = raw_value.lower()
        if "ft" in text or "feet" in text or "'" in text:
            return value * 0.3048
        if "mm" in text:
            return value / 1000.0
        if "cm" in text:
            return value / 100.0
        return value
    return None


def _parse_levels(tags):
    for key in ("building:levels", "levels"):
        value = _parse_numeric(tags.get(key))
        if value is not None and value > 0:
            return value
    return None


def _get_building_height_m(tags):
    explicit_height = _parse_height_m(tags)
    if explicit_height is not None:
        return explicit_height, "height"

    levels = _parse_levels(tags)
    if levels is not None:
        return ((levels - 1.0) * 2.95) + 3.25, "levels"

    return 12.0, "default"


def _collect_building_height_sample_points(feature):
    samples = []
    sample_keys = set()

    def _add_sample(point):
        if point is None or len(point) < 2:
            return
        key = (round(float(point[0]), 7), round(float(point[1]), 7))
        if key in sample_keys:
            return
        sample_keys.add(key)
        samples.append((float(point[0]), float(point[1])))

    outer_ring = feature.get("outer") or []
    centroid = _latlon_ring_centroid(outer_ring)
    _add_sample(centroid)

    outer_points = outer_ring[:-1] if len(outer_ring) > 1 and _point_key(outer_ring[0]) == _point_key(outer_ring[-1]) else list(outer_ring)
    if outer_points:
        step = max(1, int(math.floor(len(outer_points) / float(max(1, DSM_DTM_SAMPLE_POINT_LIMIT - 1)))))
        for index in range(0, len(outer_points), step):
            _add_sample(outer_points[index])
            if len(samples) >= DSM_DTM_SAMPLE_POINT_LIMIT:
                break

    return samples[:DSM_DTM_SAMPLE_POINT_LIMIT]


def _get_building_height_from_dsm_dtm(feature):
    sample_points = _collect_building_height_sample_points(feature)
    if not sample_points:
        return None, None

    dtm_samples = []
    dsm_samples = []
    for lat, lon in sample_points:
        try:
            dtm_value = _sample_uk_dtm_elevation(lat, lon)
        except Exception:
            dtm_value = None
        try:
            dsm_value = _sample_uk_dsm_elevation(lat, lon)
        except Exception:
            dsm_value = None
        if dtm_value is not None:
            dtm_samples.append(float(dtm_value))
        if dsm_value is not None:
            dsm_samples.append(float(dsm_value))

    if not dtm_samples or not dsm_samples:
        return None, None

    dtm_samples.sort()
    dsm_samples.sort()
    ground_elevation = dtm_samples[len(dtm_samples) // 2]
    roof_elevation = dsm_samples[-1]
    height_m = roof_elevation - ground_elevation
    if height_m <= 0:
        return None, None

    height_m = max(DSM_DTM_MIN_BUILDING_HEIGHT_M, min(DSM_DTM_MAX_BUILDING_HEIGHT_M, height_m))
    return height_m, "dsm-dtm"


def _build_dsm_dtm_dryrun_lines(building_features, fast_mode):
    if not building_features:
        return []

    explicit_count = 0
    levels_count = 0
    default_features = []
    for feature in building_features:
        _, source = _get_building_height_m(feature.get("tags") or {})
        if source == "height":
            explicit_count += 1
        elif source == "levels":
            levels_count += 1
        else:
            default_features.append(feature)

    lines = [
        "DSM-DTM dry run:",
        "  OSM explicit height: {}".format(explicit_count),
        "  OSM levels fallback: {}".format(levels_count),
        "  Buildings missing OSM height: {}".format(len(default_features)),
    ]

    if fast_mode:
        lines.append("  Skipped because Fast Mode is enabled.")
        return lines

    preview_features = list(default_features[:DSM_DTM_DRYRUN_LIMIT])
    lines.append("  Preview sample size: {}{}".format(
        len(preview_features),
        " of {}".format(len(default_features)) if len(default_features) > len(preview_features) else "",
    ))

    if not preview_features:
        lines.append("  No DSM-DTM fallback buildings to preview.")
        return lines

    successes = []
    failures = []
    for feature in preview_features:
        feature_id = feature.get("id") or "building"
        try:
            height_m, _ = _get_building_height_from_dsm_dtm(feature)
        except Exception:
            height_m = None
        if height_m is None:
            failures.append(feature_id)
        else:
            successes.append((feature_id, float(height_m)))

    lines.append("  DSM-DTM resolved: {}".format(len(successes)))
    lines.append("  DSM-DTM unresolved: {}".format(len(failures)))

    if successes:
        resolved_heights = [item[1] for item in successes]
        lines.append(
            "  Resolved height range: {:.2f} m to {:.2f} m (avg {:.2f} m)".format(
                min(resolved_heights),
                max(resolved_heights),
                sum(resolved_heights) / float(len(resolved_heights)),
            )
        )
        for feature_id, height_m in successes[:5]:
            lines.append("    {} -> {:.2f} m".format(feature_id, height_m))

    if failures:
        for feature_id in failures[:5]:
            lines.append("    {} -> unresolved".format(feature_id))

    return lines


def _road_width_m(tags):
    explicit = _parse_numeric(tags.get("width"))
    if explicit is not None and explicit > 0:
        return explicit

    highway = (tags.get("highway") or "").strip().lower()
    widths = {
        "motorway": 18.0,
        "trunk": 14.0,
        "primary": 12.0,
        "secondary": 10.0,
        "tertiary": 8.0,
        "residential": 7.2,
        "service": 6.0,
        "living_street": 5.0,
        "unclassified": 6.5,
    }
    return widths.get(highway, 7.0)


def _track_width_m(tags):
    explicit = _parse_numeric(tags.get("width"))
    if explicit is not None and explicit > 0:
        return explicit

    railway = (tags.get("railway") or "").strip().lower()
    widths = {
        "rail": 5.0,
        "light_rail": 4.0,
        "tram": 3.5,
        "subway": 5.0,
        "narrow_gauge": 3.0,
    }
    return widths.get(railway, 4.0)


def _waterway_width_m(tags):
    explicit = _parse_numeric(tags.get("width"))
    if explicit is not None and explicit > 0:
        return explicit

    waterway = (tags.get("waterway") or "").strip().lower()
    widths = {
        "river": 12.0,
        "canal": 8.0,
        "stream": 4.0,
        "ditch": 2.5,
        "drain": 2.5,
    }
    return widths.get(waterway, 5.0)


def _normalize_vector(dx, dy):
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None
    return dx / length, dy / length


def _line_intersection(point_a1, point_a2, point_b1, point_b2):
    x1, y1 = point_a1
    x2, y2 = point_a2
    x3, y3 = point_b1
    x4, y4 = point_b2
    denominator = ((x1 - x2) * (y3 - y4)) - ((y1 - y2) * (x3 - x4))
    if abs(denominator) <= 1e-9:
        return None

    det_a = (x1 * y2) - (y1 * x2)
    det_b = (x3 * y4) - (y3 * x4)
    px = ((det_a * (x3 - x4)) - ((x1 - x2) * det_b)) / denominator
    py = ((det_a * (y3 - y4)) - ((y1 - y2) * det_b)) / denominator
    return px, py


def _segment_normal(point_a, point_b, left_side):
    vector = _normalize_vector(point_b[0] - point_a[0], point_b[1] - point_a[1])
    if vector is None:
        return None
    ux, uy = vector
    if left_side:
        return -uy, ux
    return uy, -ux


def _offset_point(prev_point, point, next_point, half_width, left_side):
    prev_normal = _segment_normal(prev_point, point, left_side)
    next_normal = _segment_normal(point, next_point, left_side)

    if prev_normal is None and next_normal is None:
        return point
    if prev_normal is None:
        return point[0] + (next_normal[0] * half_width), point[1] + (next_normal[1] * half_width)
    if next_normal is None:
        return point[0] + (prev_normal[0] * half_width), point[1] + (prev_normal[1] * half_width)

    prev_start = (prev_point[0] + (prev_normal[0] * half_width), prev_point[1] + (prev_normal[1] * half_width))
    prev_end = (point[0] + (prev_normal[0] * half_width), point[1] + (prev_normal[1] * half_width))
    next_start = (point[0] + (next_normal[0] * half_width), point[1] + (next_normal[1] * half_width))
    next_end = (next_point[0] + (next_normal[0] * half_width), next_point[1] + (next_normal[1] * half_width))

    intersection = _line_intersection(prev_start, prev_end, next_start, next_end)
    if intersection is not None and math.hypot(intersection[0] - point[0], intersection[1] - point[1]) <= (half_width * 8.0):
        return intersection

    blended = _normalize_vector(prev_normal[0] + next_normal[0], prev_normal[1] + next_normal[1])
    if blended is not None:
        dot_value = max(0.2, min(1.0, (blended[0] * prev_normal[0]) + (blended[1] * prev_normal[1])))
        scale = half_width / dot_value
        return point[0] + (blended[0] * scale), point[1] + (blended[1] * scale)

    return point[0] + (prev_normal[0] * half_width), point[1] + (prev_normal[1] * half_width)


def _polyline_to_buffer_polygon(points, width_m):
    clean = _remove_duplicate_xy(points, tolerance_m=0.05)
    if len(clean) < 2:
        return None

    if clean[0] == clean[-1]:
        clean = clean[:-1]
    if len(clean) < 2:
        return None

    half_width = max(0.5, width_m / 2.0)
    left_points = []
    right_points = []

    for index, point in enumerate(clean):
        if index == 0:
            left_normal = _segment_normal(clean[0], clean[1], True)
            right_normal = _segment_normal(clean[0], clean[1], False)
            left_points.append((point[0] + (left_normal[0] * half_width), point[1] + (left_normal[1] * half_width)))
            right_points.append((point[0] + (right_normal[0] * half_width), point[1] + (right_normal[1] * half_width)))
        elif index == len(clean) - 1:
            left_normal = _segment_normal(clean[-2], clean[-1], True)
            right_normal = _segment_normal(clean[-2], clean[-1], False)
            left_points.append((point[0] + (left_normal[0] * half_width), point[1] + (left_normal[1] * half_width)))
            right_points.append((point[0] + (right_normal[0] * half_width), point[1] + (right_normal[1] * half_width)))
        else:
            left_points.append(_offset_point(clean[index - 1], point, clean[index + 1], half_width, True))
            right_points.append(_offset_point(clean[index - 1], point, clean[index + 1], half_width, False))

    polygon = left_points + list(reversed(right_points))
    polygon = _close_ring(_remove_duplicate_xy(polygon, tolerance_m=0.05))
    unique_points = {_point_key(point) for point in polygon[:-1]} if len(polygon) > 1 else set()
    if len(polygon) < 4 or len(unique_points) < 3:
        return None
    return polygon


def _meters_to_internal(value_m):
    try:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_m), DB.UnitTypeId.Meters)
    except Exception:
        return DB.UnitUtils.ConvertToInternalUnits(float(value_m), DB.DisplayUnitType.DUT_METERS)


def _pick_base_level(doc):
    levels = list(DB.FilteredElementCollector(doc).OfClass(DB.Level).WhereElementIsNotElementType())
    levels.sort(key=lambda level: level.Elevation)
    return levels[0] if levels else None


def _get_floor_types(doc):
    floor_types = list(DB.FilteredElementCollector(doc).OfClass(DB.FloorType))
    floor_types.sort(key=lambda floor_type: _el_name(floor_type).lower())
    return floor_types


def _pick_floor_type(doc, keywords):
    floor_types = _get_floor_types(doc)
    if not floor_types:
        return None

    lowered_keywords = [keyword.lower() for keyword in (keywords or [])]
    for floor_type in floor_types:
        name = _el_name(floor_type).lower()
        if any(keyword in name for keyword in lowered_keywords):
            return floor_type

    for floor_type in floor_types:
        name = _el_name(floor_type).lower()
        if "foundation" not in name and "slab edge" not in name:
            return floor_type

    return floor_types[0]


def _xy_ring_to_curve_loop(points, clockwise, base_elevation):
    ring = _close_ring(_remove_duplicate_xy(points, tolerance_m=0.01))
    unique_points = {_point_key(point) for point in ring[:-1]} if len(ring) > 1 else set()
    if len(ring) < 4 or len(unique_points) < 3:
        return None

    ordered_points = _orient_ring(ring, clockwise=clockwise)
    curve_loop = DB.CurveLoop()
    for index in range(len(ordered_points) - 1):
        start_point = ordered_points[index]
        end_point = ordered_points[index + 1]
        start_xyz = DB.XYZ(_meters_to_internal(start_point[0]), _meters_to_internal(start_point[1]), base_elevation)
        end_xyz = DB.XYZ(_meters_to_internal(end_point[0]), _meters_to_internal(end_point[1]), base_elevation)
        if start_xyz.IsAlmostEqualTo(end_xyz):
            continue
        curve_loop.Append(DB.Line.CreateBound(start_xyz, end_xyz))

    return curve_loop if curve_loop.GetExactLength() > 0 else None


def _feature_to_curve_loops(feature, origin_lat, origin_lon, base_elevation, extra_inner_rings=None):
    outer_points = _project_ring(feature.get("outer") or [], origin_lat, origin_lon)
    outer_loop = _xy_ring_to_curve_loop(outer_points, clockwise=False, base_elevation=base_elevation)
    if outer_loop is None:
        return None

    curve_loops = List[DB.CurveLoop]()
    curve_loops.Add(outer_loop)

    inner_rings = list(feature.get("inners") or [])
    inner_rings.extend(extra_inner_rings or [])

    for inner_ring in inner_rings:
        inner_points = _project_ring(inner_ring, origin_lat, origin_lon)
        inner_loop = _xy_ring_to_curve_loop(inner_points, clockwise=True, base_elevation=base_elevation)
        if inner_loop is not None:
            curve_loops.Add(inner_loop)

    return curve_loops


def _collect_water_hole_rings_for_feature(feature, hole_features):
    if not feature or not hole_features:
        return []

    feature_outer = feature.get("outer") or []
    feature_inners = list(feature.get("inners") or [])
    accepted_holes = []

    for hole_feature in hole_features:
        hole_outer = hole_feature.get("outer") or []
        if not hole_outer:
            continue

        centroid = _latlon_ring_centroid(hole_outer)
        if centroid is None or not _point_in_ring_latlon(centroid, feature_outer):
            continue
        if any(_point_in_ring_latlon(centroid, inner_ring) for inner_ring in feature_inners):
            continue
        if any(_point_in_ring_latlon(centroid, accepted_ring) for accepted_ring in accepted_holes):
            continue

        accepted_holes.append(hole_outer)

    return accepted_holes


def _polygon_to_curve_loops(outer_points, inner_rings, base_elevation):
    outer_loop = _xy_ring_to_curve_loop(outer_points, clockwise=False, base_elevation=base_elevation)
    if outer_loop is None:
        return None

    curve_loops = List[DB.CurveLoop]()
    curve_loops.Add(outer_loop)

    for inner_points in inner_rings or []:
        inner_loop = _xy_ring_to_curve_loop(inner_points, clockwise=True, base_elevation=base_elevation)
        if inner_loop is not None:
            curve_loops.Add(inner_loop)

    return curve_loops


def _point_in_ring_xy(point, ring):
    x = point[0]
    y = point[1]
    inside = False
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        intersects = (y1 > y) != (y2 > y)
        if not intersects:
            continue
        slope_x = ((x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12)) + x1
        if x < slope_x:
            inside = not inside
    return inside


def _pick_toposolid_type(doc, keywords=None):
    topo_cls = getattr(DB, "ToposolidType", None)
    if topo_cls is None:
        return None

    topo_types = list(DB.FilteredElementCollector(doc).OfClass(topo_cls))
    topo_types.sort(key=lambda item: _el_name(item).lower())
    if not topo_types:
        return None

    lowered_keywords = [keyword.lower() for keyword in (keywords or [])]
    for topo_type in topo_types:
        name = _el_name(topo_type).lower()
        if any(keyword in name for keyword in lowered_keywords):
            return topo_type
    return topo_types[0]


def _create_toposolid(doc, boundary_loop, terrain_grid, center_lat, center_lon, level, topo_type):
    topo_cls = getattr(DB, "Toposolid", None)
    if topo_cls is None:
        raise Exception("This Revit version does not support Toposolid.")

    profile_loops = List[DB.CurveLoop]()
    profile_loops.Add(boundary_loop)

    topo_points = List[DB.XYZ]()
    min_elevation_m = terrain_grid["min_elevation_m"]
    for point in terrain_grid.get("points") or []:
        x_m, y_m = _project_latlon(point["lat"], point["lon"], center_lat, center_lon)
        z_value = level.Elevation + _meters_to_internal(point["elevation_m"] - min_elevation_m)
        topo_points.Add(DB.XYZ(_meters_to_internal(x_m), _meters_to_internal(y_m), z_value))

    return topo_cls.Create(doc, profile_loops, topo_points, topo_type.Id, level.Id)


def _create_toposolid_subdivision(doc, host_toposolid, curve_loops, comment_text):
    subdivision = None
    creation_attempts = []

    host_method = getattr(host_toposolid, "CreateSubDivision", None)
    if host_method is not None:
        creation_attempts.extend(
            [
                lambda: host_method(doc, curve_loops),
                lambda: host_method(curve_loops),
            ]
        )

    topo_cls = getattr(DB, "Toposolid", None)
    static_method = getattr(topo_cls, "CreateSubDivision", None) if topo_cls is not None else None
    if static_method is not None:
        creation_attempts.extend(
            [
                lambda: static_method(doc, host_toposolid.Id, curve_loops),
                lambda: static_method(doc, host_toposolid, curve_loops),
            ]
        )

    errors = []
    for attempt in creation_attempts:
        try:
            subdivision = attempt()
            if subdivision is not None:
                _set_comment(subdivision, comment_text)
                return subdivision
        except Exception as ex:
            errors.append(str(ex))

    raise Exception("Failed to create toposolid subdivision. {}".format("; ".join(errors[:5])))


def _get_revit_version_year(doc):
    try:
        return int(str(doc.Application.VersionNumber).strip())
    except Exception:
        return 0


def _set_toposolid_subdivision_offset(subdivision, offset_m):
    if subdivision is None:
        return False

    normalized_candidates = ("subdivideoffset",)
    built_in_param = getattr(DB.BuiltInParameter, "TOPOSOLID_SUBDIVIDE_HEIGHT", None)

    if built_in_param is not None:
        try:
            parameter = subdivision.get_Parameter(built_in_param)
            if parameter and not parameter.IsReadOnly and parameter.StorageType == DB.StorageType.Double:
                parameter.Set(_meters_to_internal(offset_m))
                return True
        except Exception:
            pass

    try:
        for parameter in subdivision.Parameters:
            try:
                definition = parameter.Definition
                name = definition.Name if definition is not None else ""
                normalized_name = re.sub(r"[^a-z]", "", (name or "").lower())
                if normalized_name not in normalized_candidates:
                    continue
                if parameter.IsReadOnly or parameter.StorageType != DB.StorageType.Double:
                    continue
                parameter.Set(_meters_to_internal(offset_m))
                return True
            except Exception:
                pass
    except Exception:
        pass

    for parameter_name in ("Sub-divide Offset", "Sub-Divide Offset", "Subdivide Offset"):
        try:
            parameter = subdivision.LookupParameter(parameter_name)
            if parameter and not parameter.IsReadOnly and parameter.StorageType == DB.StorageType.Double:
                parameter.Set(_meters_to_internal(offset_m))
                return True
        except Exception:
            pass

    return False


def _apply_subdivision_offset_2026(doc, subdivision):
    if _get_revit_version_year(doc) >= 2026:
        _set_toposolid_subdivision_offset(subdivision, TERRAIN_SUBDIVISION_OFFSET_2026_M)


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


def _revit_color(rgb):
    red, green, blue = rgb
    return DB.Color(int(red), int(green), int(blue))


def _is_invalid_element_id(value):
    try:
        return isinstance(value, DB.ElementId) and value == DB.ElementId.InvalidElementId
    except Exception:
        return False


def _get_solid_fill_pattern_id(doc):
    try:
        for pattern in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement):
            fill_pattern = pattern.GetFillPattern()
            if fill_pattern and fill_pattern.IsSolidFill:
                return pattern.Id
    except Exception:
        pass
    return DB.ElementId.InvalidElementId


def _ensure_material(doc, name, rgb, transparency=0):
    material = None
    for candidate in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        if candidate.Name == name:
            material = candidate
            break

    if material is None:
        material_id = DB.Material.Create(doc, name)
        material = doc.GetElement(material_id)

    if material is None:
        return DB.ElementId.InvalidElementId

    color = _revit_color(rgb)
    solid_fill_id = _get_solid_fill_pattern_id(doc)

    try:
        material.Color = color
    except Exception:
        pass

    for attr_name, attr_value in (
        ("Transparency", max(0, min(100, int(transparency or 0)))),
        ("SurfaceForegroundPatternColor", color),
        ("CutForegroundPatternColor", color),
        ("SurfaceBackgroundPatternColor", color),
        ("CutBackgroundPatternColor", color),
        ("SurfacePatternColor", color),
        ("CutPatternColor", color),
        ("SurfaceForegroundPatternId", solid_fill_id),
        ("CutForegroundPatternId", solid_fill_id),
        ("SurfaceBackgroundPatternId", solid_fill_id),
        ("CutBackgroundPatternId", solid_fill_id),
        ("SurfacePatternId", solid_fill_id),
        ("CutPatternId", solid_fill_id),
    ):
        if _is_invalid_element_id(attr_value):
            continue
        try:
            setattr(material, attr_name, attr_value)
        except Exception:
            pass

    return material.Id


def _set_material_parameter(element, material_id):
    if element is None or material_id is None or material_id == DB.ElementId.InvalidElementId:
        return False

    for built_in_param in (
        DB.BuiltInParameter.MATERIAL_ID_PARAM,
        DB.BuiltInParameter.STRUCTURAL_MATERIAL_PARAM,
    ):
        try:
            parameter = element.get_Parameter(built_in_param)
            if parameter and not parameter.IsReadOnly and parameter.StorageType == DB.StorageType.ElementId:
                parameter.Set(material_id)
                return True
        except Exception:
            pass

    for param_name in ("Material", "Structural Material"):
        try:
            parameter = element.LookupParameter(param_name)
            if parameter and not parameter.IsReadOnly and parameter.StorageType == DB.StorageType.ElementId:
                parameter.Set(material_id)
                return True
        except Exception:
            pass

    return False


def _apply_active_view_override(doc, element, rgb, transparency=0):
    if doc is None or element is None:
        return

    try:
        view = doc.ActiveView
    except Exception:
        view = None

    if view is None:
        return

    try:
        if getattr(view, "IsTemplate", False):
            return
    except Exception:
        pass

    solid_fill_id = _get_solid_fill_pattern_id(doc)
    color = _revit_color(rgb)
    overrides = DB.OverrideGraphicSettings()

    for setter, value in (
        ("SetSurfaceForegroundPatternColor", color),
        ("SetSurfaceBackgroundPatternColor", color),
        ("SetCutForegroundPatternColor", color),
        ("SetCutBackgroundPatternColor", color),
        ("SetProjectionFillColor", color),
        ("SetCutFillColor", color),
        ("SetSurfaceTransparency", max(0, min(100, int(transparency or 0)))),
    ):
        try:
            getattr(overrides, setter)(value)
        except Exception:
            pass

    if solid_fill_id != DB.ElementId.InvalidElementId:
        for setter in (
            "SetSurfaceForegroundPatternId",
            "SetSurfaceBackgroundPatternId",
            "SetCutForegroundPatternId",
            "SetCutBackgroundPatternId",
            "SetProjectionFillPatternId",
            "SetCutFillPatternId",
        ):
            try:
                getattr(overrides, setter)(solid_fill_id)
            except Exception:
                pass

    try:
        view.SetElementOverrides(element.Id, overrides)
    except Exception:
        pass


def _apply_palette(doc, element, palette_entry, apply_view_override=True):
    if element is None or not palette_entry:
        return

    material_id = palette_entry.get("material_id")
    assigned = _set_material_parameter(element, material_id)
    if not assigned:
        try:
            type_id = element.GetTypeId()
            if type_id and type_id != DB.ElementId.InvalidElementId:
                _set_material_parameter(doc.GetElement(type_id), material_id)
        except Exception:
            pass

    if apply_view_override:
        _apply_active_view_override(
            doc,
            element,
            palette_entry.get("rgb") or (186, 186, 186),
            palette_entry.get("transparency") or 0,
        )


def _ensure_palette(doc):
    palette = {}
    for layer_name, palette_entry in CONTEXT_PALETTE.items():
        layer_palette = dict(palette_entry)
        layer_palette["material_id"] = _ensure_material(
            doc,
            layer_palette["name"],
            layer_palette["rgb"],
            layer_palette.get("transparency") or 0,
        )
        palette[layer_name] = layer_palette
    return palette


def _create_solid(curve_loops, height_m, material_id):
    height_internal = _meters_to_internal(height_m)
    if height_internal <= 0:
        raise Exception("Computed height is not positive.")

    if material_id is not None and material_id != DB.ElementId.InvalidElementId:
        try:
            solid_options = DB.SolidOptions(material_id, DB.ElementId.InvalidElementId)
            return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
                curve_loops,
                DB.XYZ.BasisZ,
                height_internal,
                solid_options,
            )
        except Exception:
            pass

    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(
        curve_loops,
        DB.XYZ.BasisZ,
        height_internal,
    )


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


def _make_safe_name(text, fallback):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text or "")
    safe = safe.strip("_.")
    return safe or fallback


def _find_mass_family_template(app):
    template_root = getattr(app, "FamilyTemplatePath", None)
    if not template_root or not os.path.isdir(template_root):
        return None

    candidates = []
    for root, _, files in os.walk(template_root):
        for file_name in files:
            lowered_name = file_name.lower()
            if not lowered_name.endswith(".rft"):
                continue
            if "mass" not in lowered_name:
                continue
            full_path = os.path.join(root, file_name)
            lowered_path = full_path.lower()
            score = 100
            if "conceptual mass" in lowered_path:
                score = 0
            elif "metric" in lowered_name and "mass" in lowered_name:
                score = 10
            elif lowered_name == "mass.rft":
                score = 20
            candidates.append((score, len(full_path), full_path))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][2]


def _create_directshape_building(doc, solid, feature_id, comment_text, palette_entry=None):
    errors = []
    for built_in_category in (DB.BuiltInCategory.OST_Mass, DB.BuiltInCategory.OST_GenericModel):
        shape = None
        try:
            shape = DB.DirectShape.CreateElement(doc, DB.ElementId(built_in_category))
            shape.ApplicationId = APP_ID
            shape.ApplicationDataId = feature_id or "building"

            geometry = List[DB.GeometryObject]()
            geometry.Add(solid)
            shape.SetShape(geometry)
            _set_comment(shape, comment_text)
            _apply_palette(doc, shape, palette_entry)
            return shape
        except Exception as ex:
            errors.append("{}: {}".format(str(built_in_category), str(ex)))
            if shape is not None:
                try:
                    doc.Delete(shape.Id)
                except Exception:
                    pass

    raise Exception("; ".join(errors))


def _prepare_building_mass_geometry(feature, origin_lat, origin_lon, base_elevation, building_output_mode=BUILDING_OUTPUT_DIRECTSHAPE, palette_entry=None, fast_mode=False):
    height_m, height_source = _get_building_height_m(feature.get("tags") or {})
    if (not fast_mode) and height_source == "default":
        terrain_height_m, terrain_height_source = _get_building_height_from_dsm_dtm(feature)
        if terrain_height_m is not None:
            height_m = terrain_height_m
            height_source = terrain_height_source
    if height_m <= 0:
        raise Exception("Building height resolved to a non-positive value.")

    curve_loops = _feature_to_curve_loops(feature, origin_lat, origin_lon, base_elevation)
    if curve_loops is None or curve_loops.Count == 0:
        raise Exception("Failed to build a valid building footprint.")

    material_id = DB.ElementId.InvalidElementId
    if building_output_mode == BUILDING_OUTPUT_DIRECTSHAPE and palette_entry:
        material_id = palette_entry.get("material_id")
    solid = _create_solid(curve_loops, height_m, material_id)
    feature_id = feature.get("id") or "building"
    comment_text = "UK Context Builder | {} | {:.2f} m | {}".format(feature_id, height_m, height_source)
    return {
        "solid": solid,
        "curve_loops": curve_loops,
        "feature_id": feature_id,
        "comment_text": comment_text,
        "height_m": height_m,
        "base_elevation": base_elevation,
    }


def _create_inplace_mass_buildings(doc, building_items, palette_entry=None):
    return _create_mass_family_buildings(doc, building_items, palette_entry)


def _chunk_items(items, chunk_size):
    if not items:
        return []
    if chunk_size <= 0:
        return [list(items)]
    return [list(items[index:index + chunk_size]) for index in range(0, len(items), chunk_size)]


def _create_family_freeform(family_doc, building_item):
    freeform_cls = getattr(DB, "FreeFormElement", None)
    if freeform_cls is None:
        raise Exception("This Revit version does not support FreeFormElement creation.")

    solid = building_item.get("solid")
    if solid is None:
        raise Exception("Building solid is missing for family creation.")

    freeform = freeform_cls.Create(family_doc, solid)
    if freeform is None:
        raise Exception("Revit did not create the family freeform element.")

    _set_comment(freeform, building_item.get("comment_text") or "")
    return freeform


def _create_mass_family_chunk(doc, building_items, palette_entry=None, batch_index=0, batch_count=1):
    if not building_items:
        raise Exception("No building solids were provided for family creation.")

    app = getattr(doc, "Application", None)
    if app is None:
        raise Exception("Unable to access the Revit application.")

    template_path = _find_mass_family_template(app)
    if not template_path:
        template_root = getattr(app, "FamilyTemplatePath", None) or "<not set>"
        raise Exception(
            "No mass family template was found under Revit's Family Template Path.\n"
            "Current path: {}\n"
            "Set Revit's Family Template Path to a location that contains a Conceptual Mass template and try again."
            .format(template_root)
        )

    if batch_count > 1:
        family_name_seed = "WWP_ContextMass_Batch_{}_part_{:02d}_of_{:02d}".format(len(building_items), batch_index + 1, batch_count)
    else:
        family_name_seed = "WWP_ContextMass_Batch_{}".format(len(building_items))
    family_name = _make_safe_name(family_name_seed, "WWP_ContextMass_Batch")
    family_doc = app.NewFamilyDocument(template_path)
    if family_doc is None:
        raise Exception("Failed to open a new mass family document from template.")

    cache_dir = _ensure_cache_dir("generated_mass_families")
    family_path = os.path.join(cache_dir, "{}.rfa".format(family_name))
    family = None
    family_transaction = None

    try:
        family_transaction = DB.Transaction(family_doc, "Create UK Context Builder Mass")
        family_transaction.Start()

        owner_family = getattr(family_doc, "OwnerFamily", None)
        if owner_family is not None:
            try:
                owner_family.Name = family_name
            except Exception:
                pass

        for index, building_item in enumerate(building_items):
            freeform = _create_family_freeform(family_doc, building_item)
            if freeform is None:
                raise Exception("Failed to create the family geometry for building {}.".format(index + 1))
        family_transaction.Commit()
        family_transaction = None

        save_options = DB.SaveAsOptions()
        save_options.OverwriteExistingFile = True
        family_doc.SaveAs(family_path, save_options)

        try:
            family = family_doc.LoadFamily(doc, _OverwriteFamilyLoadOptions())
        except Exception:
            family = None
    except Exception:
        if family_transaction is not None:
            try:
                family_transaction.RollBack()
            except Exception:
                pass
        raise
    finally:
        try:
            family_doc.Close(False)
        except Exception:
            pass

    if not isinstance(family, DB.Family):
        family = None

    if family is None:
        try:
            families = list(DB.FilteredElementCollector(doc).OfClass(DB.Family))
            for candidate in families:
                if candidate.Name == family_name:
                    family = candidate
                    break
        except Exception:
            family = None

    if family is None:
        raise Exception("Failed to load the generated mass family into the project.")

    symbol_ids = list(family.GetFamilySymbolIds())
    if not symbol_ids:
        raise Exception("The generated mass family contains no types.")

    symbol = doc.GetElement(symbol_ids[0])
    if symbol is None:
        raise Exception("Failed to access the generated mass family type.")

    try:
        if hasattr(symbol, "IsActive") and not symbol.IsActive:
            symbol.Activate()
            doc.Regenerate()
    except Exception:
        pass

    creation_attempts = [
        lambda: doc.Create.NewFamilyInstance(DB.XYZ(0.0, 0.0, 0.0), symbol, DB.Structure.StructuralType.NonStructural),
        lambda: doc.Create.NewFamilyInstance(DB.XYZ(0.0, 0.0, 0.0), symbol, doc.ActiveView),
        lambda: doc.Create.NewFamilyInstance(DB.XYZ(0.0, 0.0, 0.0), symbol, doc.GetElement(doc.ActiveView.GenLevel.Id), DB.Structure.StructuralType.NonStructural),
    ]
    errors = []
    for attempt in creation_attempts:
        try:
            instance = attempt()
            if instance is not None:
                _set_comment(instance, "UK Context Builder | Batched mass family | {} buildings".format(len(building_items)))
                _apply_palette(doc, instance, palette_entry)
                return instance
        except Exception as ex:
            errors.append(str(ex))

    raise Exception("Failed to place the generated mass family instance. {}".format("; ".join(errors[:5])))


def _create_mass_family_buildings(doc, building_items, palette_entry=None):
    batches = _chunk_items(building_items, MAX_BUILDING_SOLIDS_PER_MASS_FAMILY)
    if not batches:
        raise Exception("No building solids were provided for family creation.")

    instances = []
    errors = []
    batch_count = len(batches)
    for batch_index, batch_items in enumerate(batches):
        try:
            instance = _create_mass_family_chunk(
                doc,
                batch_items,
                palette_entry,
                batch_index=batch_index,
                batch_count=batch_count,
            )
            if instance is not None:
                instances.append(instance)
        except Exception as ex:
            errors.append("batch {} of {}: {}".format(batch_index + 1, batch_count, str(ex)))

    if errors:
        raise Exception("Failed to create one or more mass family batches. {}".format("; ".join(errors[:5])))

    return instances


def _create_building_mass(doc, feature, origin_lat, origin_lon, base_elevation, palette_entry=None, building_output_mode=BUILDING_OUTPUT_DIRECTSHAPE, fast_mode=False):
    building_item = _prepare_building_mass_geometry(feature, origin_lat, origin_lon, base_elevation, building_output_mode, palette_entry, fast_mode)
    return _create_directshape_building(
        doc,
        building_item["solid"],
        building_item["feature_id"],
        building_item["comment_text"],
        palette_entry,
    )


def _create_floor(doc, curve_loops, floor_type, level, comment_text, offset_m=0.0):
    if curve_loops is None or curve_loops.Count == 0:
        raise Exception("No valid floor boundary loops were produced.")

    floor = None
    try:
        floor = DB.Floor.Create(doc, curve_loops, floor_type.Id, level.Id)
    except Exception:
        curve_array = DB.CurveArray()
        for curve in curve_loops[0]:
            curve_array.Append(curve)
        floor = doc.Create.NewFloor(curve_array, floor_type, level, False)

    if floor is None:
        raise Exception("Revit did not create a floor element.")

    try:
        if floor_type is not None and floor.GetTypeId() != floor_type.Id:
            floor.ChangeTypeId(floor_type.Id)
    except Exception:
        pass

    _set_comment(floor, comment_text)

    try:
        offset_param = floor.get_Parameter(DB.BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
        if offset_param and not offset_param.IsReadOnly:
            offset_param.Set(_meters_to_internal(offset_m))
    except Exception:
        pass

    return floor


def _get_floor_type_total_width(compound):
    return _meters_to_internal(CONTEXT_FLOOR_THICKNESS_M)


def _context_floor_type_specs():
    return (
        ("WWP CONTEXT - ROAD", "roads"),
        ("WWP CONTEXT - TRACK", "tracks"),
        ("WWP CONTEXT - PARCEL", "parcels"),
        ("WWP CONTEXT - PARK", "parks"),
        ("WWP CONTEXT - WATER", "water"),
    )


def _legacy_floor_type_suffix(type_name):
    if not type_name or " - " not in type_name:
        return None
    return " - WWP " + type_name.split(" - ")[-1].title()


def _apply_compound_material_ids(compound, material_id):
    if compound is None or material_id is None or _is_invalid_element_id(material_id):
        return compound
    try:
        layers = list(compound.GetLayers())
    except Exception:
        layers = []
    for index in range(len(layers)):
        try:
            compound.SetMaterialId(index, material_id)
        except Exception:
            pass
    return compound


def _build_context_floor_layers(material_id):
    net_layers = List[DB.CompoundStructureLayer]()
    net_layers.Add(
        DB.CompoundStructureLayer(
            _meters_to_internal(CONTEXT_FLOOR_THICKNESS_M),
            DB.MaterialFunctionAssignment.Structure,
            material_id,
        )
    )
    return net_layers


def _update_floor_type_material(floor_type, material_id):
    if floor_type is None or material_id is None or _is_invalid_element_id(material_id):
        return floor_type

    compound = None
    set_layers_error = None
    create_error = None
    try:
        compound = floor_type.GetCompoundStructure()
    except Exception:
        compound = None

    net_layers = _build_context_floor_layers(material_id)

    if compound is not None:
        try:
            compound.SetLayers(net_layers)
            compound = _apply_compound_material_ids(compound, material_id)
        except Exception as exc:
            set_layers_error = str(exc)
            compound = None

    if compound is None:
        try:
            compound = DB.CompoundStructure.CreateSimpleCompoundStructure(net_layers)
            compound = _apply_compound_material_ids(compound, material_id)
        except Exception as exc:
            create_error = str(exc)
            compound = None

    if compound is None:
        raise Exception(
            "Compound structure update failed. {}{}".format(
                "SetLayers failed: {}. ".format(set_layers_error) if set_layers_error else "",
                "CreateSimpleCompoundStructure failed: {}.".format(create_error) if create_error else "",
            ).strip()
        )

    floor_type.SetCompoundStructure(compound)

    try:
        verify_compound = floor_type.GetCompoundStructure()
        verify_compound = _apply_compound_material_ids(verify_compound, material_id)
        floor_type.SetCompoundStructure(verify_compound)
    except Exception:
        pass

    return floor_type


def _repair_existing_context_floor_types(doc, palette):
    if doc is None or not palette:
        return

    target_specs = {}
    for type_name, palette_key in _context_floor_type_specs():
        palette_entry = (palette or {}).get(palette_key)
        material_id = (palette_entry or {}).get("material_id")
        if material_id is None or _is_invalid_element_id(material_id):
            continue
        target_specs[type_name] = {
            "palette_key": palette_key,
            "material_id": material_id,
            "legacy_suffix": _legacy_floor_type_suffix(type_name),
        }

    if not target_specs:
        return

    try:
        floor_types = list(DB.FilteredElementCollector(doc).OfClass(DB.FloorType))
    except Exception:
        floor_types = []

    for floor_type in floor_types:
        try:
            floor_type_name = floor_type.Name
        except Exception:
            continue

        matched_type_name = None
        for target_name, spec in target_specs.items():
            if floor_type_name == target_name or (spec["legacy_suffix"] and floor_type_name.endswith(spec["legacy_suffix"])):
                matched_type_name = target_name
                break

        if matched_type_name is None:
            continue

        spec = target_specs[matched_type_name]
        try:
            if floor_type_name != matched_type_name:
                if not any(candidate.Name == matched_type_name for candidate in floor_types if candidate.Id != floor_type.Id):
                    floor_type.Name = matched_type_name
        except Exception:
            pass

        try:
            _update_floor_type_material(floor_type, spec["material_id"])
        except Exception:
            pass


def _get_materialized_floor_type(doc, base_floor_type, palette_entry, type_name):
    if base_floor_type is None or not palette_entry:
        return base_floor_type

    material_id = palette_entry.get("material_id")
    if material_id is None or _is_invalid_element_id(material_id):
        return base_floor_type

    floor_type = None
    legacy_suffix = _legacy_floor_type_suffix(type_name)
    try:
        for candidate in DB.FilteredElementCollector(doc).OfClass(DB.FloorType):
            candidate_name = candidate.Name
            if candidate_name == type_name or (legacy_suffix and candidate_name.endswith(legacy_suffix)):
                floor_type = candidate
                break
    except Exception:
        pass

    if floor_type is None:
        try:
            duplicated = base_floor_type.Duplicate(type_name)
            if isinstance(duplicated, DB.FloorType):
                floor_type = duplicated
            else:
                floor_type = doc.GetElement(duplicated)
        except Exception:
            return base_floor_type

    if floor_type is None:
        return base_floor_type

    try:
        if floor_type.Name != type_name:
            floor_type.Name = type_name
    except Exception:
        pass

    try:
        _update_floor_type_material(floor_type, material_id)
    except Exception:
        pass

    return floor_type


def _summarize_data(address_label, radius_m, dense_area_m, fast_mode, endpoint, selected_layers, building_features, roads, tracks, parcel_features, park_features, water_features, waterways, dsm_dtm_dryrun_lines=None):
    selected_layer_names = []
    if (selected_layers or {}).get("buildings"):
        selected_layer_names.append("Buildings")
    if (selected_layers or {}).get("roads"):
        selected_layer_names.append("Roads")
    if (selected_layers or {}).get("tracks"):
        selected_layer_names.append("Tracks")
    if (selected_layers or {}).get("parcels"):
        selected_layer_names.append("Parcels")
    if (selected_layers or {}).get("parks"):
        selected_layer_names.append("Parks")
    if (selected_layers or {}).get("water"):
        selected_layer_names.append("Water")
    if (selected_layers or {}).get("terrain"):
        selected_layer_names.append("Terrain")

    lines = [
        "Location: {}".format(address_label),
        "Radius: {} m".format(int(round(radius_m))),
        "Overpass endpoint: {}".format(endpoint or "<unknown>"),
        "Building output: DirectShape",
        "Import mode: {}".format("Fast" if fast_mode else "Accurate"),
        "Selected layers: {}".format(", ".join(selected_layer_names) if selected_layer_names else "<none>"),
        "",
        "Buildings: {}".format(len(building_features or [])),
        "Road centerlines: {}".format(len(roads or [])),
        "Track centerlines: {}".format(len(tracks or [])),
        "Parcels: {}".format(len(parcel_features or [])),
        "Parks: {}".format(len(park_features or [])),
        "Water areas: {}".format(len(water_features or [])),
        "Waterways: {}".format(len(waterways or [])),
        "",
        "Terrain uses a denser central UK EA DTM sample block of {} m x {} m.".format(
            int(round(dense_area_m)),
            int(round(dense_area_m)),
        ) if (dense_area_m and dense_area_m > 0 and not fast_mode) else "Terrain uses the faster coarse-only UK EA DTM sampling mode.",
        "Missing building heights will use UK EA DSM minus DTM when available." if not fast_mode else "Missing building heights will use the default fallback height in Fast Mode.",
        "Buildings will be created as DirectShape in the Mass category when possible.",
        "Roads, tracks, parcels, parks, and water will be created as Toposolid subdivisions when Terrain is enabled.",
        "Roads, tracks, parcels, parks, and water will be created as Floors when Terrain is disabled.",
    ]
    if dsm_dtm_dryrun_lines:
        lines.append("")
        lines.extend(dsm_dtm_dryrun_lines)
    return "\n".join(lines)


def _summarize_results(created_counts, failures):
    lines = [
        "Terrain created: {}".format(created_counts.get("terrain", 0)),
        "Buildings created: {}".format(created_counts.get("buildings", 0)),
        "Road elements created: {}".format(created_counts.get("roads", 0)),
        "Track elements created: {}".format(created_counts.get("tracks", 0)),
        "Parcel elements created: {}".format(created_counts.get("parcels", 0)),
        "Park elements created: {}".format(created_counts.get("parks", 0)),
        "Water elements created: {}".format(created_counts.get("water", 0)),
        "Failures: {}".format(len(failures)),
    ]

    if failures:
        lines.append("")
        lines.append("Failures (first {}):".format(MAX_FAILURES_IN_REPORT))
        for failure in failures[:MAX_FAILURES_IN_REPORT]:
            lines.append("{} | {}".format(failure["id"], failure["reason"]))

    return "\n".join(lines)


def _project_way_points(way, origin_lat, origin_lon):
    points = _geometry_to_points(way.get("geometry"))
    projected = [_project_latlon(point[0], point[1], origin_lat, origin_lon) for point in points]
    return _remove_duplicate_xy(projected, tolerance_m=0.05)


def _is_area_way(tags):
    area_value = (tags.get("area") or "").strip().lower()
    return area_value in ("yes", "1", "true") or bool(tags.get("area:highway"))


def _build_terrain_boundary_loop(center_lat, center_lon, radius_m, base_elevation):
    min_lat, min_lon, max_lat, max_lon = _calculate_square_bounds(center_lat, center_lon, radius_m)
    boundary_points = [
        _project_latlon(min_lat, min_lon, center_lat, center_lon),
        _project_latlon(min_lat, max_lon, center_lat, center_lon),
        _project_latlon(max_lat, max_lon, center_lat, center_lon),
        _project_latlon(max_lat, min_lon, center_lat, center_lon),
    ]
    boundary_points.append(boundary_points[0])
    return _xy_ring_to_curve_loop(boundary_points, clockwise=False, base_elevation=base_elevation)


def main():
    doc = _get_doc()
    if doc is None:
        UI.TaskDialog.Show(TITLE, "No active Revit document found.")
        return

    user_inputs = _show_dialog(doc)
    if user_inputs is None:
        return

    address = user_inputs.get("address") or ""
    radius_m = user_inputs.get("radius_m") or DEFAULT_RADIUS_M
    dense_area_m = user_inputs.get("dense_area_m")
    if dense_area_m is None:
        dense_area_m = TERRAIN_DENSE_SQUARE_SIZE_M
    fast_mode = bool(user_inputs.get("fast_mode"))
    run_mode = (user_inputs.get("mode") or "run").strip().lower()
    selected_layers = user_inputs.get("layers") or {}
    selected_location = user_inputs.get("location")
    selected_location_label = user_inputs.get("location_label") or ""
    terrain_enabled = bool(selected_layers.get("terrain"))

    use_selected_location = selected_location is not None and (not address or address == selected_location_label)
    if use_selected_location:
        center_lat, center_lon = selected_location
        address_label = address or selected_location_label or "Selected map location ({:.6f}, {:.6f})".format(center_lat, center_lon)
    elif address:
        center_lat, center_lon = _geocode_address(address)
        address_label = address
    else:
        location = _get_project_site_location(doc)
        if location is None:
            UI.TaskDialog.Show(TITLE, "Enter an address or set the project site location in Revit.")
            return
        center_lat, center_lon = location
        address_label = "Project site location ({:.6f}, {:.6f})".format(center_lat, center_lon)

    query = _build_overpass_query(center_lat, center_lon, radius_m)
    overpass_data = _fetch_overpass_json(query)
    elements = overpass_data.get("elements") or []
    if not elements and not terrain_enabled:
        UI.TaskDialog.Show(TITLE, "No OSM context data was returned for the selected location.")
        return

    ways, relations, roads, tracks, waterways = _collect_elements(elements)
    building_features = _build_area_features(ways, relations, _is_building_tag) if selected_layers.get("buildings") else []
    parcel_features = _build_area_features(ways, relations, _is_parcel_tag) if selected_layers.get("parcels") else []
    park_features = _build_area_features(ways, relations, _is_park_tag) if selected_layers.get("parks") else []
    water_features = _build_area_features(ways, relations, _is_water_tag) if selected_layers.get("water") else []
    roads = roads if selected_layers.get("roads") else []
    tracks = tracks if selected_layers.get("tracks") else []
    waterways = waterways if selected_layers.get("water") else []
    dsm_dtm_dryrun_lines = _build_dsm_dtm_dryrun_lines(building_features, fast_mode) if selected_layers.get("buildings") else []

    preview_text = _summarize_data(
        address_label,
        radius_m,
        dense_area_m,
        fast_mode,
        overpass_data.get("_endpoint"),
        selected_layers,
        building_features,
        roads,
        tracks,
        parcel_features,
        park_features,
        water_features,
        waterways,
        dsm_dtm_dryrun_lines,
    )
    if run_mode == "dryrun":
        ui.uiUtils_show_text_report(
            "{} - Dry Run".format(TITLE),
            preview_text,
            ok_text="Close",
            cancel_text=None,
            width=760,
            height=560,
        )
        return

    if not ui.uiUtils_show_text_report(
        "{} - Preview".format(TITLE),
        preview_text,
        ok_text="Run Import",
        cancel_text="Cancel",
        width=760,
        height=560,
    ):
        return

    base_level = _pick_base_level(doc)
    if base_level is None:
        UI.TaskDialog.Show(TITLE, "No Revit levels were found in the active model.")
        return

    road_floor_type = _pick_floor_type(doc, ["road", "street", "asphalt", "pavement", "concrete"]) if roads and not terrain_enabled else None
    track_floor_type = _pick_floor_type(doc, ["rail", "track", "ballast", "concrete"]) if tracks and not terrain_enabled else None
    parcel_floor_type = _pick_floor_type(doc, ["site", "parcel", "landscape", "generic"]) if parcel_features and not terrain_enabled else None
    park_floor_type = _pick_floor_type(doc, ["park", "landscape", "grass", "green"]) if park_features and not terrain_enabled else None
    water_floor_type = _pick_floor_type(doc, ["water", "pond", "pool", "generic"]) if (water_features or waterways) and not terrain_enabled else None
    terrain_topo_type = _pick_toposolid_type(doc, ["topo", "terrain", "earth", "site"]) if terrain_enabled else None
    if ((roads and not terrain_enabled) and road_floor_type is None) or ((tracks and not terrain_enabled) and track_floor_type is None) or ((parcel_features and not terrain_enabled) and parcel_floor_type is None) or ((park_features and not terrain_enabled) and park_floor_type is None) or ((((water_features or waterways) and not terrain_enabled)) and water_floor_type is None):
        UI.TaskDialog.Show(TITLE, "No floor types were found in the active model.")
        return
    if terrain_enabled and terrain_topo_type is None:
        UI.TaskDialog.Show(TITLE, "No Toposolid types were found in the active model, or this Revit version does not support Toposolid.")
        return

    terrain_grid = None
    if terrain_enabled:
        try:
            terrain_grid = _build_terrain_grid(center_lat, center_lon, radius_m, dense_area_m, fast_mode=fast_mode)
        except Exception as ex:
            UI.TaskDialog.Show(TITLE, "UK terrain sampling failed:\n{}".format(str(ex)))
            return

    created_counts = {"terrain": 0, "buildings": 0, "roads": 0, "tracks": 0, "parcels": 0, "parks": 0, "water": 0}
    failures = []
    base_elevation = base_level.Elevation
    terrain_toposolid = None

    transaction = DB.Transaction(doc, TITLE)
    started = False
    try:
        transaction.Start()
        started = True
        palette = _ensure_palette(doc)
        _repair_existing_context_floor_types(doc, palette)
        road_floor_type = _get_materialized_floor_type(doc, road_floor_type, palette.get("roads"), "WWP CONTEXT - ROAD") if road_floor_type is not None else None
        track_floor_type = _get_materialized_floor_type(doc, track_floor_type, palette.get("tracks"), "WWP CONTEXT - TRACK") if track_floor_type is not None else None
        parcel_floor_type = _get_materialized_floor_type(doc, parcel_floor_type, palette.get("parcels"), "WWP CONTEXT - PARCEL") if parcel_floor_type is not None else None
        park_floor_type = _get_materialized_floor_type(doc, park_floor_type, palette.get("parks"), "WWP CONTEXT - PARK") if park_floor_type is not None else None
        water_floor_type = _get_materialized_floor_type(doc, water_floor_type, palette.get("water"), "WWP CONTEXT - WATER") if water_floor_type is not None else None

        if terrain_enabled:
            boundary_loop = _build_terrain_boundary_loop(center_lat, center_lon, radius_m, base_elevation)
            if boundary_loop is None:
                raise Exception("Failed to build a terrain boundary profile.")
            terrain_toposolid = _create_toposolid(
                doc,
                boundary_loop,
                terrain_grid,
                center_lat,
                center_lon,
                base_level,
                terrain_topo_type,
            )
            _set_comment(
                terrain_toposolid,
                "UK Context Builder | UK EA terrain | range {:.2f}m to {:.2f}m".format(
                    terrain_grid["min_elevation_m"],
                    terrain_grid["max_elevation_m"],
                ),
            )
            _apply_palette(doc, terrain_toposolid, palette.get("terrain"))
            created_counts["terrain"] = 1

        for feature in building_features:
            try:
                feature_base_elevation = base_elevation
                if terrain_enabled and terrain_grid is not None:
                    centroid_latlon = _latlon_ring_centroid(feature.get("outer") or [])
                    if centroid_latlon is not None:
                        sampled_elevation = _terrain_bilinear_elevation(
                            terrain_grid,
                            center_lat,
                            center_lon,
                            centroid_latlon[0],
                            centroid_latlon[1],
                        )
                        if sampled_elevation is not None:
                            feature_base_elevation = base_level.Elevation + _meters_to_internal(
                                sampled_elevation - terrain_grid["min_elevation_m"]
                            )

                _create_building_mass(
                    doc,
                    feature,
                    center_lat,
                    center_lon,
                    feature_base_elevation,
                    palette.get("buildings"),
                    BUILDING_OUTPUT_DIRECTSHAPE,
                    fast_mode=fast_mode,
                )
                created_counts["buildings"] += 1
            except Exception as ex:
                failures.append({"id": feature.get("id") or "building", "reason": str(ex)})

        for way in roads:
            way_id = "way/{}".format(way.get("id"))
            try:
                tags = way.get("tags") or {}
                projected = _project_way_points(way, center_lat, center_lon)
                if len(projected) < 2:
                    raise Exception("Road geometry is too short.")

                if _is_area_way(tags):
                    ring = _close_ring(projected)
                    curve_loops = _polygon_to_curve_loops(ring, [], base_elevation)
                else:
                    polygon = _polyline_to_buffer_polygon(projected, _road_width_m(tags))
                    curve_loops = _polygon_to_curve_loops(polygon, [], base_elevation) if polygon else None

                if terrain_enabled and terrain_toposolid is not None:
                    road_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Road subdivision | {}".format(way_id),
                    )
                    _apply_subdivision_offset_2026(doc, road_subdivision)
                    _apply_palette(doc, road_subdivision, palette.get("roads"))
                else:
                    road_floor = _create_floor(
                        doc,
                        curve_loops,
                        road_floor_type,
                        base_level,
                        "UK Context Builder | Road | {}".format(way_id),
                    )
                    _apply_palette(doc, road_floor, palette.get("roads"), apply_view_override=False)
                created_counts["roads"] += 1
            except Exception as ex:
                failures.append({"id": way_id, "reason": str(ex)})

        for way in tracks:
            way_id = "way/{}".format(way.get("id"))
            try:
                tags = way.get("tags") or {}
                projected = _project_way_points(way, center_lat, center_lon)
                if len(projected) < 2:
                    raise Exception("Track geometry is too short.")

                if _is_area_way(tags):
                    ring = _close_ring(projected)
                    curve_loops = _polygon_to_curve_loops(ring, [], base_elevation)
                else:
                    polygon = _polyline_to_buffer_polygon(projected, _track_width_m(tags))
                    curve_loops = _polygon_to_curve_loops(polygon, [], base_elevation) if polygon else None

                if terrain_enabled and terrain_toposolid is not None:
                    track_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Track subdivision | {}".format(way_id),
                    )
                    _apply_subdivision_offset_2026(doc, track_subdivision)
                    _apply_palette(doc, track_subdivision, palette.get("tracks"))
                else:
                    track_floor = _create_floor(
                        doc,
                        curve_loops,
                        track_floor_type,
                        base_level,
                        "UK Context Builder | Track | {}".format(way_id),
                    )
                    _apply_palette(doc, track_floor, palette.get("tracks"), apply_view_override=False)
                created_counts["tracks"] += 1
            except Exception as ex:
                failures.append({"id": way_id, "reason": str(ex)})

        for feature in parcel_features:
            try:
                curve_loops = _feature_to_curve_loops(feature, center_lat, center_lon, base_elevation)
                if terrain_enabled and terrain_toposolid is not None:
                    parcel_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Parcel subdivision | {}".format(feature.get("id")),
                    )
                    _apply_subdivision_offset_2026(doc, parcel_subdivision)
                    _apply_palette(doc, parcel_subdivision, palette.get("parcels"))
                else:
                    parcel_floor = _create_floor(
                        doc,
                        curve_loops,
                        parcel_floor_type,
                        base_level,
                        "UK Context Builder | Parcel | {}".format(feature.get("id")),
                    )
                    _apply_palette(doc, parcel_floor, palette.get("parcels"), apply_view_override=False)
                created_counts["parcels"] += 1
            except Exception as ex:
                failures.append({"id": feature.get("id") or "parcel", "reason": str(ex)})

        for feature in park_features:
            try:
                curve_loops = _feature_to_curve_loops(feature, center_lat, center_lon, base_elevation)
                if terrain_enabled and terrain_toposolid is not None:
                    park_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Park subdivision | {}".format(feature.get("id")),
                    )
                    _apply_subdivision_offset_2026(doc, park_subdivision)
                    _apply_palette(doc, park_subdivision, palette.get("parks"))
                else:
                    park_floor = _create_floor(
                        doc,
                        _feature_to_curve_loops(
                            feature,
                            center_lat,
                            center_lon,
                            base_elevation,
                            extra_inner_rings=_collect_water_hole_rings_for_feature(feature, water_features),
                        ),
                        park_floor_type,
                        base_level,
                        "UK Context Builder | Park | {}".format(feature.get("id")),
                    )
                    _apply_palette(doc, park_floor, palette.get("parks"), apply_view_override=False)
                created_counts["parks"] += 1
            except Exception as ex:
                failures.append({"id": feature.get("id") or "park", "reason": str(ex)})

        for feature in water_features:
            try:
                curve_loops = _feature_to_curve_loops(feature, center_lat, center_lon, base_elevation)
                if terrain_enabled and terrain_toposolid is not None:
                    water_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Water subdivision | {}".format(feature.get("id")),
                    )
                    _apply_subdivision_offset_2026(doc, water_subdivision)
                    _apply_palette(doc, water_subdivision, palette.get("water"))
                else:
                    water_floor = _create_floor(
                        doc,
                        curve_loops,
                        water_floor_type,
                        base_level,
                        "UK Context Builder | Water Area | {}".format(feature.get("id")),
                    )
                    _apply_palette(doc, water_floor, palette.get("water"), apply_view_override=False)
                created_counts["water"] += 1
            except Exception as ex:
                failures.append({"id": feature.get("id") or "water", "reason": str(ex)})

        for way in waterways:
            way_id = "way/{}".format(way.get("id"))
            tags = way.get("tags") or {}
            if _is_water_tag(tags):
                continue
            try:
                projected = _project_way_points(way, center_lat, center_lon)
                polygon = _polyline_to_buffer_polygon(projected, _waterway_width_m(tags))
                curve_loops = _polygon_to_curve_loops(polygon, [], base_elevation) if polygon else None
                if terrain_enabled and terrain_toposolid is not None:
                    waterway_subdivision = _create_toposolid_subdivision(
                        doc,
                        terrain_toposolid,
                        curve_loops,
                        "UK Context Builder | Waterway subdivision | {}".format(way_id),
                    )
                    _apply_subdivision_offset_2026(doc, waterway_subdivision)
                    _apply_palette(doc, waterway_subdivision, palette.get("water"))
                else:
                    waterway_floor = _create_floor(
                        doc,
                        curve_loops,
                        water_floor_type,
                        base_level,
                        "UK Context Builder | Waterway | {}".format(way_id),
                    )
                    _apply_palette(doc, waterway_floor, palette.get("water"), apply_view_override=False)
                created_counts["water"] += 1
            except Exception as ex:
                failures.append({"id": way_id, "reason": str(ex)})

        transaction.Commit()
    except Exception as ex:
        if started:
            try:
                transaction.RollBack()
            except Exception:
                pass
        UI.TaskDialog.Show(TITLE + " - Error", "{}\n\n{}".format(ex, traceback.format_exc()))
        return

    ui.uiUtils_show_text_report(
        "{} - Results".format(TITLE),
        _summarize_results(created_counts, failures),
        ok_text="Close",
        cancel_text=None,
        width=760,
        height=560,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        UI.TaskDialog.Show(TITLE + " - Error", "{}\n\n{}".format(exc, traceback.format_exc()))
