"""Tiered location resolution for Lebanese place names.

Tiers, tried in order until one produces an answer:
  1. exact     - normalized match against a gazetteer of known areas
  2. substring - whole-word substring match against the same gazetteer
  3. geocoder  - OpenStreetMap Nominatim, restricted to Lebanon
  4. embedding - cosine similarity of OpenAI embeddings (catches typos like "Achrafiye")

The gazetteer is built from the schools layer itself: every cadastral area (village /
neighbourhood) and caza (district) that appears in it, positioned at the mean location
of its schools. It is cached in data/ after the first run.
"""

import json
import re
import threading
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Literal, Optional

import numpy as np
import requests
from langchain_core.tools import tool

from config import DATA_DIR, EMBEDDING_MODEL, get_openai_client, schools_layer_url
from models.entities import ResolvedLocation
from tools.distance import haversine_km

GAZETTEER_PATH = DATA_DIR / "gazetteer.json"
EMBEDDINGS_PATH = DATA_DIR / "gazetteer_embeddings.npz"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "COE749-lebanon-emergency-planner/1.0 (course project)"}
NOMINATIM_MIN_INTERVAL_S = 1.1  # Nominatim usage policy: max 1 request per second

SAME_PLACE_KM = 5.0          # geocoder hits closer than this are treated as one place
# Embedding thresholds, calibrated on gemini-embedding-2: typos score 0.74-0.97 against the
# right name ('Hermell' 0.74, 'Beirutt' 0.79, 'Achrafiye' 0.97), while junk tops out lower
# ('Paris' 0.64, 'hospital' 0.57, 'xyzqq' 0.46). Other embedding models need re-calibration.
EMBED_ACCEPT = 0.72          # top similarity needed to accept an embedding match outright
EMBED_MARGIN = 0.05          # ...and how far ahead of the best differently-named area it must be
EMBED_SUGGEST = 0.68         # below this, embedding matches are not even offered as candidates
MAX_CANDIDATES = 5
EMBED_BATCH = 100            # Gemini's OpenAI-compatible endpoint caps batch size at 100
EMBED_QUOTA_RETRIES = 4

ARTICLES = {"el", "al", "ed", "es", "ech", "en", "er", "ez", "the"}

Status = Literal["resolved", "ambiguous", "not_found"]


@dataclass
class GeocodeOutcome:
    status: Status
    method: Optional[str] = None
    location: Optional[ResolvedLocation] = None
    candidates: List[ResolvedLocation] = field(default_factory=list)
    message: str = ""


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation and Arabic-style articles: 'El-Mansouriyeh' -> 'mansouriyeh'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return " ".join(t for t in tokens if t not in ARTICLES)


# --------------------------------------------------------------------------- gazetteer


def _build_gazetteer() -> List[Dict]:
    resp = requests.get(
        f"{schools_layer_url()}/query",
        params={"where": "1=1", "outFields": "Cadastral,Caza,Governorate", "outSR": 4326, "f": "json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS error while building gazetteer: {data['error']}")

    cadastrals: Dict[tuple, list] = defaultdict(list)
    cazas: Dict[str, list] = defaultdict(list)
    for feat in data["features"]:
        a, g = feat["attributes"], feat.get("geometry") or {}
        name, caza = (a.get("Cadastral") or "").strip(), (a.get("Caza") or "").strip()
        if "x" not in g or name in ("", "NA") or caza in ("", "NA"):
            continue
        cadastrals[(name, caza, (a.get("Governorate") or "").strip())].append((g["y"], g["x"]))
        cazas[caza].append((g["y"], g["x"]))

    def centroid(points):
        return float(np.mean([p[0] for p in points])), float(np.mean([p[1] for p in points]))

    entries = []
    for (name, caza, gov), pts in sorted(cadastrals.items()):
        lat, lon = centroid(pts)
        entries.append({"name": name, "caza": caza, "governorate": gov, "kind": "cadastral",
                        "label": f"{name} ({caza})", "lat": lat, "lon": lon})
    for caza, pts in sorted(cazas.items()):
        lat, lon = centroid(pts)
        entries.append({"name": caza, "caza": caza, "governorate": None, "kind": "caza",
                        "label": f"{caza} District", "lat": lat, "lon": lon})
    return entries


@lru_cache
def load_gazetteer() -> List[Dict]:
    if GAZETTEER_PATH.exists():
        return json.loads(GAZETTEER_PATH.read_text(encoding="utf-8"))
    entries = _build_gazetteer()
    DATA_DIR.mkdir(exist_ok=True)
    GAZETTEER_PATH.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
    return entries


def _to_location(entry: Dict, source: str, confidence: float) -> ResolvedLocation:
    return ResolvedLocation(name=entry["label"], lat=entry["lat"], lon=entry["lon"], caza=entry["caza"],
                            source=source, confidence=round(confidence, 3))


def _prefer_specific(entries: List[Dict]) -> List[Dict]:
    """A town and its district often share a name (e.g. Baalbek); the town is the useful answer."""
    towns = [e for e in entries if e["kind"] == "cadastral"]
    return towns or entries


def _from_matches(entries: List[Dict], method: str, confidence: float, query: str) -> GeocodeOutcome:
    entries = _prefer_specific(entries)
    locs = [_to_location(e, method, confidence) for e in entries[:MAX_CANDIDATES]]
    if len(entries) == 1:
        return GeocodeOutcome("resolved", method, locs[0], message=f"'{query}' matched {locs[0].name}")
    return GeocodeOutcome("ambiguous", method, candidates=locs,
                          message=f"'{query}' matches {len(entries)} known areas")


def _match_exact(query: str) -> Optional[GeocodeOutcome]:
    q = normalize(query)
    hits = [e for e in load_gazetteer() if q in (normalize(e["name"]), normalize(e["label"]))]
    return _from_matches(hits, "exact", 1.0, query) if hits else None


def _match_substring(query: str) -> Optional[GeocodeOutcome]:
    q = normalize(query)
    if len(q) < 3:
        return None
    pattern = re.compile(rf"\b{re.escape(q)}\b")
    hits = [e for e in load_gazetteer() if pattern.search(normalize(e["name"]))]
    # Shorter names are closer to what was typed ('Zahle Midan' before 'Zahle Haouche Al-Zaraane').
    hits.sort(key=lambda e: len(e["name"]))
    return _from_matches(hits, "substring", 0.85, query) if hits else None


# --------------------------------------------------------------------------- geocoder

_nominatim_lock = threading.Lock()
_nominatim_last_call = 0.0


@lru_cache(maxsize=256)
def _nominatim_search(query: str) -> tuple:
    global _nominatim_last_call
    with _nominatim_lock:
        wait = NOMINATIM_MIN_INTERVAL_S - (time.monotonic() - _nominatim_last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={"q": query, "countrycodes": "lb", "format": "jsonv2", "limit": 8, "accept-language": "en"},
                headers=NOMINATIM_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json()
        except (requests.RequestException, ValueError):
            results = []
        finally:
            _nominatim_last_call = time.monotonic()
    return tuple(results)


def _short_name(display_name: str) -> str:
    parts = [p.strip() for p in display_name.split(",")]
    parts = [p for p in parts if p and not p.isdigit() and p != "Lebanon"]
    return ", ".join(parts[:3])


def _match_geocoder(query: str) -> Optional[GeocodeOutcome]:
    results = _nominatim_search(query)
    places: List[ResolvedLocation] = []
    for r in results:  # Nominatim returns results best-first
        lat, lon = float(r["lat"]), float(r["lon"])
        if any(haversine_km(lat, lon, p.lat, p.lon) < SAME_PLACE_KM for p in places):
            continue
        places.append(ResolvedLocation(name=_short_name(r["display_name"]), lat=lat, lon=lon,
                                       source="geocoder", confidence=0.8))
    if not places:
        return None
    if len(places) == 1:
        return GeocodeOutcome("resolved", "geocoder", places[0], message=f"Nominatim found {places[0].name}")
    return GeocodeOutcome("ambiguous", "geocoder", candidates=places[:MAX_CANDIDATES],
                          message=f"Nominatim found {len(places)} distinct places called '{query}'")


# --------------------------------------------------------------------------- embeddings


def _embed_batch(texts: List[str]) -> np.ndarray:
    from openai import RateLimitError

    # SDK retries are off: on a quota error each retry re-sends the whole batch and counts
    # against the quota again. Per-minute limits are waited out here instead.
    client = get_openai_client().with_options(max_retries=0)
    for attempt in range(EMBED_QUOTA_RETRIES):
        try:
            resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            break
        except RateLimitError as exc:
            if "PerDay" in str(exc) or attempt == EMBED_QUOTA_RETRIES - 1:
                raise
            delay = re.search(r"retry in ([\d.]+)s", str(exc))
            wait = float(delay.group(1)) + 1 if delay else 60.0
            print(f"  (embedding quota reached; waiting {wait:.0f}s)", flush=True)
            time.sleep(wait)
    arr = np.array([d.embedding for d in resp.data], dtype=np.float32)
    return arr / np.linalg.norm(arr, axis=1, keepdims=True)


def _embed(text: str) -> np.ndarray:
    return _embed_batch([text])[0]


def _load_vector_cache() -> Dict[str, np.ndarray]:
    if not EMBEDDINGS_PATH.exists():
        return {}
    cached = np.load(EMBEDDINGS_PATH)
    if str(cached["model"]) != EMBEDDING_MODEL:
        return {}
    return dict(zip(cached["names"].tolist(), cached["vectors"]))


@lru_cache
def _gazetteer_vectors() -> np.ndarray:
    """One vector per gazetteer entry. Built once, saved after every batch so an interrupted build resumes."""
    names = [e["name"] for e in load_gazetteer()]
    cache = _load_vector_cache()
    missing = sorted(set(names) - cache.keys())
    if missing:
        print(f"  (embedding {len(missing)} gazetteer names with {EMBEDDING_MODEL}; one-time, cached in data/)")
        DATA_DIR.mkdir(exist_ok=True)
    for i in range(0, len(missing), EMBED_BATCH):
        batch = missing[i:i + EMBED_BATCH]
        cache.update(zip(batch, _embed_batch(batch)))
        np.savez(EMBEDDINGS_PATH, model=EMBEDDING_MODEL, names=np.array(list(cache)),
                 vectors=np.stack(list(cache.values())))
    return np.stack([cache[n] for n in names])


def _match_embedding(query: str) -> Optional[GeocodeOutcome]:
    gazetteer = load_gazetteer()
    scores = _gazetteer_vectors() @ _embed(query)
    order = np.argsort(-scores)
    top = [(gazetteer[i], float(scores[i])) for i in order[:MAX_CANDIDATES]]
    best_entry, best = top[0]
    runner_up = next((s for e, s in top[1:] if e["name"] != best_entry["name"]), 0.0)
    same_name = [e for e, _ in top if e["name"] == best_entry["name"]]

    if best >= EMBED_ACCEPT and best - runner_up >= EMBED_MARGIN:
        # e.g. a typo of 'Mejdlaya' still has to be disambiguated between Aley and Zgharta
        return _from_matches(same_name, "embedding", best, query)
    suggestions = [_to_location(e, "embedding", s) for e, s in top if s >= EMBED_SUGGEST]
    if suggestions:
        return GeocodeOutcome("ambiguous", "embedding", candidates=suggestions,
                              message=f"No confident match for '{query}'; closest known areas by similarity")
    return None


# --------------------------------------------------------------------------- entry points


def resolve_location_text(query: str) -> GeocodeOutcome:
    query = (query or "").strip()
    if not query:
        return GeocodeOutcome("not_found", message="No location was mentioned")
    for tier in (_match_exact, _match_substring, _match_geocoder):
        outcome = tier(query)
        if outcome:
            return outcome
    try:
        outcome = _match_embedding(query)
    except Exception as exc:  # e.g. embedding quota exhausted: degrade to "not found", don't crash the graph
        return GeocodeOutcome("not_found", message=f"Could not find '{query}' (fuzzy matching unavailable: {type(exc).__name__})")
    return outcome or GeocodeOutcome("not_found", message=f"Could not find '{query}' in Lebanon")


@tool(response_format="content_and_artifact")
def geocode_location(place: str):
    """Resolve a place name in Lebanon (city, town, neighbourhood or district) to coordinates.

    Returns status 'resolved' with one location, 'ambiguous' with candidate locations,
    or 'not_found'. Pass only the place name, e.g. 'Hamra' or 'Mejdlaya (Zgharta)'.
    """
    outcome = resolve_location_text(place)
    content = {
        "status": outcome.status,
        "method": outcome.method,
        "message": outcome.message,
        "location": outcome.location.model_dump() if outcome.location else None,
        "candidates": [c.name for c in outcome.candidates],
    }
    return json.dumps(content, ensure_ascii=False), outcome
