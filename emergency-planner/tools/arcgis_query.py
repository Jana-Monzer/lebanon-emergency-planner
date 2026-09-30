"""Spatial queries against the two public ArcGIS feature layers.

Field names below were verified against the live layers:
- Schools (SchoolForEmergencyPlan_Map/FeatureServer/0): the shelter flag is the Arabic
  field ``مركز_ايواء`` ("shelter centre") holding 'Yes' or ''. ``وضع_المدرسة`` (school
  status) is ' ', 'مقفلة' (closed) or 'موجودة مرتين' (listed twice - a duplicate record).
- Hospitals (Hospital_Labs/FeatureServer/0): the layer mixes hospitals and labs; real
  hospitals have ``Facility_T`` = 'Hospitals , مستشفيات'. There is no emergency-room field.
"""

import json
from typing import Any, Dict, List, Optional

import requests
from langchain_core.tools import tool

from config import hospitals_layer_url, schools_layer_url
from models.entities import Hospital, SchoolShelter
from tools.distance import haversine_km

SHELTER_FIELD = "مركز_ايواء"
STATUS_FIELD = "وضع_المدرسة"
PHONE_FIELD = "رقم_الهاتف"
DUPLICATE_STATUS = "موجودة مرتين"
STATUS_LABELS = {"مقفلة": "closed"}
HOSPITAL_TYPE = "Hospitals , مستشفيات"

SHELTER_WHERE = f"{SHELTER_FIELD} = 'Yes' AND ({STATUS_FIELD} IS NULL OR {STATUS_FIELD} <> '{DUPLICATE_STATUS}')"
HOSPITAL_WHERE = f"Facility_T = '{HOSPITAL_TYPE}'"

MIN_RADIUS_KM = 0.5
MAX_RADIUS_KM = 50.0
MAX_RESULTS = 10
TIMEOUT_S = 20


class ArcGISQueryError(RuntimeError):
    pass


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _yes_no(value: Any) -> Optional[bool]:
    text = (_clean(value) or "").lower()
    return {"yes": True, "no": False}.get(text)


def _clamp_radius(radius_km: float) -> float:
    return min(max(float(radius_km), MIN_RADIUS_KM), MAX_RADIUS_KM)


def _query_near(layer_url: str, where: str, lat: float, lon: float, radius_km: float) -> List[Dict[str, Any]]:
    """Features within ``radius_km`` of the point, each with a ``distance_km`` key added."""
    params = {
        "where": where,
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": radius_km,
        "units": "esriSRUnit_Kilometer",
        "outFields": "*",
        "outSR": 4326,
        "returnGeometry": "true",
        "f": "json",
    }
    try:
        resp = requests.get(f"{layer_url}/query", params=params, timeout=TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ArcGISQueryError(f"ArcGIS request failed: {exc}") from exc
    if "error" in data:
        raise ArcGISQueryError(f"ArcGIS error: {data['error']}")

    features = []
    for feat in data.get("features", []):
        geom = feat.get("geometry") or {}
        if "x" not in geom or "y" not in geom:
            continue
        dist = haversine_km(lat, lon, geom["y"], geom["x"])
        # The server-side buffer is approximate; re-check with an exact great-circle distance.
        if dist <= radius_km:
            features.append({"attributes": feat["attributes"], "lat": geom["y"], "lon": geom["x"], "distance_km": dist})
    features.sort(key=lambda f: f["distance_km"])
    return features


def find_shelters(lat: float, lon: float, radius_km: float, limit: int = 5) -> List[SchoolShelter]:
    shelters = []
    for f in _query_near(schools_layer_url(), SHELTER_WHERE, lat, lon, radius_km)[:limit]:
        a = f["attributes"]
        status = _clean(a.get(STATUS_FIELD))
        shelters.append(
            SchoolShelter(
                name=_clean(a.get("School_Name")) or _clean(a.get("School_Name___Arabic")) or "Unnamed school",
                name_ar=_clean(a.get("School_Name___Arabic")),
                cadastral=_clean(a.get("Cadastral")),
                caza=_clean(a.get("Caza")),
                governorate=_clean(a.get("Governorate")),
                lat=f["lat"],
                lon=f["lon"],
                distance_km=round(f["distance_km"], 2),
                num_classes=a.get("Total_Nb_Class"),
                phone=_clean(a.get(PHONE_FIELD)),
                status=STATUS_LABELS.get(status, status),
            )
        )
    return shelters


def find_hospitals(lat: float, lon: float, radius_km: float, limit: int = 5) -> List[Hospital]:
    hospitals = []
    for f in _query_near(hospitals_layer_url(), HOSPITAL_WHERE, lat, lon, radius_km)[:limit]:
        a = f["attributes"]
        hospitals.append(
            Hospital(
                name=_clean(a.get("Facility_E")) or _clean(a.get("Facility_A")) or "Unnamed hospital",
                name_ar=_clean(a.get("Facility_A")),
                ownership=_clean(a.get("ownership")),
                cadastral=_clean(a.get("cad_name")),
                district=_clean(a.get("district")),
                governorate=_clean(a.get("governorat")),
                lat=f["lat"],
                lon=f["lon"],
                distance_km=round(f["distance_km"], 2),
                has_lab=_yes_no(a.get("Lab")),
                has_blood_bank=_yes_no(a.get("Blood_bank")),
                has_radiology=_yes_no(a.get("Radiology")),
                phone=_clean(a.get("Phone")),
            )
        )
    return hospitals


def _run_query(finder, lat: float, lon: float, radius_km: float, max_results: int):
    """Shared body of the two LangChain tools: (content for the LLM, artifact for the state)."""
    radius_km = _clamp_radius(radius_km)
    max_results = min(max(int(max_results), 1), MAX_RESULTS)
    try:
        items = finder(lat, lon, radius_km, max_results)
        error = None
    except ArcGISQueryError as exc:
        items, error = [], str(exc)
    content = {
        "radius_km": radius_km,
        "count": len(items),
        "results": [{"name": i.name, "distance_km": i.distance_km, "lat": i.lat, "lon": i.lon} for i in items],
    }
    if error:
        content["error"] = error
    return json.dumps(content, ensure_ascii=False), {"radius_km": radius_km, "items": items, "error": error}


@tool(response_format="content_and_artifact")
def query_schools_layer(lat: float, lon: float, radius_km: float, max_results: int = 5):
    """Find schools designated as emergency shelters within radius_km of a WGS84 point, nearest first."""
    return _run_query(find_shelters, lat, lon, radius_km, max_results)


@tool(response_format="content_and_artifact")
def query_hospitals_layer(lat: float, lon: float, radius_km: float, max_results: int = 5):
    """Find hospitals within radius_km of a WGS84 point, nearest first."""
    return _run_query(find_hospitals, lat, lon, radius_km, max_results)
