"""
OpenRouteService (ORS) client: geocoding and directions.

External-call budget per request:
  * Directions: exactly 1 HTTP call (cached by endpoint coordinates).
  * Geocoding:  1 HTTP call per free-text US place name (cached by name);
                0 calls when the caller supplies "lat,lng" coordinates.
  * Cold cache: at most 3 external calls (2 geocodes + 1 route).
  * Warm cache: exactly 0 external calls.
"""

import hashlib
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings
from django.core.cache import cache

from .errors import InvalidLocation, LocationNotInUSA, RouteNotFound, UpstreamError

logger = logging.getLogger(__name__)

# Bounding box of the contiguous USA (fuel station dataset strictly covers lower 48 states)
US_LAT = (24.3, 49.6)
US_LON = (-125.1, -66.8)

_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Thread-safe persistent session with retries for transient connection drops
def _create_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=2,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_session = _create_session()

# ContextVar tracks external HTTP calls during the lifecycle of the active request
external_calls: ContextVar[int] = ContextVar("external_calls", default=0)


def _http(method: str, url: str, **kwargs) -> requests.Response:
    external_calls.set(external_calls.get() + 1)
    logger.debug("ORS External Call #%d: %s %s", external_calls.get(), method, url)
    return _session.request(method, url, **kwargs)


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str


@dataclass
class Route:
    coords: np.ndarray        # (N, 2) array of [lat, lon]
    cum_miles: np.ndarray     # (N,) cumulative miles along the route
    distance_miles: float
    duration_seconds: float


def _in_usa(lat: float, lon: float) -> bool:
    return US_LAT[0] <= lat <= US_LAT[1] and US_LON[0] <= lon <= US_LON[1]


def _headers() -> dict:
    if not settings.ORS_API_KEY:
        raise UpstreamError(
            "OpenRouteService API key is missing. Set ORS_API_KEY in your .env file."
        )
    return {"Authorization": settings.ORS_API_KEY}


def _cache_key(prefix: str, value: str) -> str:
    return prefix + hashlib.md5(value.encode("utf-8")).hexdigest()


def geocode(text: str) -> Location:
    """
    Resolve 'lat,lng' or a free-text US place name into a validated Location.
    Results are cached to minimize external API consumption.
    """
    text = (text or "").strip()
    if not text:
        raise InvalidLocation("Location must not be empty.")

    # 1. Direct coordinate format (lat,lng)
    coord_match = _COORD_RE.match(text)
    if coord_match:
        lat, lon = float(coord_match.group(1)), float(coord_match.group(2))
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise InvalidLocation(f"Invalid geographic coordinate values: '{text}'.")
        if not _in_usa(lat, lon):
            raise LocationNotInUSA(
                f"Coordinates '{text}' are outside the contiguous United States ({lat:.2f}, {lon:.2f})."
            )
        return Location(lat, lon, f"{lat:.5f},{lon:.5f}")

    # 2. Cache lookup for text queries
    ck = _cache_key("geo:", text.lower())
    cached_location = cache.get(ck)
    if cached_location:
        logger.debug("Geocode cache hit for: %r", text)
        return cached_location

    # 3. External ORS Geocoding API call
    try:
        response = _http(
            "GET",
            f"{settings.ORS_BASE_URL}/geocode/search",
            headers=_headers(),
            params={"text": text, "size": 1},
            timeout=settings.ORS_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("ORS Geocoding request failed: %s", e)
        raise UpstreamError(f"Geocoding service unreachable: {e}")

    if response.status_code == 429:
        raise UpstreamError("OpenRouteService rate limit exceeded. Please wait a moment before trying again.")
    if response.status_code in (401, 403):
        raise UpstreamError("OpenRouteService authentication failed. Please verify ORS_API_KEY.")
    if response.status_code != 200:
        raise UpstreamError(f"Geocoding failed with upstream status {response.status_code}.")

    try:
        features = response.json().get("features") or []
    except ValueError:
        raise UpstreamError("Invalid JSON received from geocoding service.")

    if not features:
        raise InvalidLocation(f"Could not find '{text}' in the United States.")

    feature = features[0]
    lon, lat = feature["geometry"]["coordinates"]
    properties = feature.get("properties", {})
    country_code = properties.get("country_a")

    if country_code != "USA" or not _in_usa(lat, lon):
        where = properties.get("label", "outside the USA")
        raise LocationNotInUSA(
            f"'{text}' resolved to '{where}', which is not in the contiguous USA. "
            "For ambiguous names, include the state (e.g. 'Paris, TX')."
        )

    location = Location(lat, lon, properties.get("label", text))
    cache.set(ck, location, settings.ROUTE_CACHE_TTL)
    return location


def haversine_miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Vectorized great-circle distance calculation in statute miles."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return 3958.7613 * 2 * np.arcsin(np.sqrt(a))


def get_route(start: Location, end: Location) -> Route:
    """
    Fetch the driving route between start and end via OpenRouteService Directions API.
    Result is cached by coordinate pair to avoid redundant upstream queries.
    """
    ck = _cache_key("route:", f"{start.lat:.4f},{start.lon:.4f}>{end.lat:.4f},{end.lon:.4f}")
    cached_route = cache.get(ck)
    if cached_route:
        logger.debug("Route cache hit for: (%f, %f) -> (%f, %f)", start.lat, start.lon, end.lat, end.lon)
        return cached_route

    try:
        response = _http(
            "POST",
            f"{settings.ORS_BASE_URL}/v2/directions/driving-car/geojson",
            headers=_headers(),
            json={
                "coordinates": [[start.lon, start.lat], [end.lon, end.lat]],
                "radiuses": [5000, 5000],  # Snap points up to 5 km to the road network
            },
            timeout=settings.ORS_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("ORS Directions request failed: %s", e)
        raise UpstreamError(f"Routing service unreachable: {e}")

    if response.status_code == 429:
        raise UpstreamError("OpenRouteService rate limit exceeded. Please wait a moment before trying again.")
    if response.status_code in (401, 403):
        raise UpstreamError("OpenRouteService authentication failed. Please verify ORS_API_KEY.")
    if response.status_code == 404 or (response.status_code == 400 and "find routable point" in response.text):
        raise RouteNotFound("No drivable route found between these locations.")
    if response.status_code != 200:
        raise UpstreamError(f"Routing service returned upstream error ({response.status_code}).")

    try:
        data = response.json()
        feature = data["features"][0]
        points = np.asarray(feature["geometry"]["coordinates"], dtype=float)[:, :2]  # [lon, lat]
        coords = points[:, ::-1].copy()                                              # [lat, lon]
        summary = feature["properties"]["summary"]
    except (ValueError, KeyError, IndexError) as err:
        logger.error("Malformed ORS routing response: %s", err)
        raise UpstreamError("Received malformed routing response from provider.")

    seg = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    distance_miles = summary["distance"] / 1609.344  # meters to miles
    duration_seconds = float(summary.get("duration", 0.0))

    route = Route(coords, cum, distance_miles, duration_seconds)
    # Scale cumulative mileage proportionally so cumulative total equals routing engine distance
    if cum[-1] > 0:
        route.cum_miles = cum * (route.distance_miles / cum[-1])

    cache.set(ck, route, settings.ROUTE_CACHE_TTL)
    return route


def plan_route(start_text: str, finish_text: str) -> Tuple[Location, Location, Route]:
    """
    Resolve origin and destination locations and compute the driving route.
    """
    start = geocode(start_text)
    end = geocode(finish_text)

    # Identical coordinates check
    if (round(start.lat, 4), round(start.lon, 4)) == (round(end.lat, 4), round(end.lon, 4)):
        raise InvalidLocation("Start and finish are the same location.")

    route = get_route(start, end)
    return start, end, route
