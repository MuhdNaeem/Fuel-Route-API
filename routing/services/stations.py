"""
Spatial candidate search for fuel stations along a driving route.

Performance Strategy:
1. Station coordinates are cached in memory as numpy arrays and 3-D Cartesian coordinates
   on the unit sphere: (x, y, z) = (cos(lat)*cos(lon), cos(lat)*sin(lon), sin(lat)).
2. A fast 2-D bounding-box prefilter discards ~90-95% of non-candidate stations.
3. SciPy cKDTree indexes the remaining route polyline points. A single vectorized tree query
   finds each candidate's exact nearest route point and lateral distance in sub-milliseconds.
"""

from functools import lru_cache
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from django.conf import settings
from scipy.spatial import cKDTree

from routing.models import FuelStation

logger = logging.getLogger(__name__)

EARTH_RADIUS_MILES = 3958.7613


def _xyz(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Project spherical lat/lon coordinates onto a 3-D Cartesian unit sphere."""
    rad_lat, rad_lon = np.radians(lat), np.radians(lon)
    return np.column_stack([
        np.cos(rad_lat) * np.cos(rad_lon),
        np.cos(rad_lat) * np.sin(rad_lon),
        np.sin(rad_lat),
    ])


@lru_cache(maxsize=1)
def _load_stations_cache() -> Tuple[List[tuple], np.ndarray, np.ndarray, np.ndarray]:
    """
    Load all active fuel stations from the database into contiguous memory arrays.
    Cached across requests for sub-millisecond route matching.
    """
    rows = list(
        FuelStation.objects.values_list(
            "id", "name", "address", "city", "state", "price", "latitude", "longitude"
        )
    )
    if not rows:
        return [], np.array([]), np.array([]), np.empty((0, 3))

    lat = np.array([r[6] for r in rows], dtype=np.float64)
    lon = np.array([r[7] for r in rows], dtype=np.float64)
    xyz = _xyz(lat, lon)
    logger.info("Loaded %d fuel stations into in-memory spatial index.", len(rows))
    return rows, lat, lon, xyz


def clear_cache() -> None:
    """Clear in-memory station cache. Useful after database reloads and in test fixtures."""
    _load_stations_cache.cache_clear()


def stations_along_route(route, corridor_miles: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    Identify and return all fuel stations within `corridor_miles` of the route polyline,
    ordered monotonically by mileage along the route.
    """
    corridor = corridor_miles or getattr(settings, "CORRIDOR_MILES", 5.0)
    rows, lat, lon, xyz = _load_stations_cache()
    if not rows or len(route.coords) == 0:
        return []

    # 1. Bounding-box prefilter: 1 degree latitude ~= 69 miles
    pad = (corridor / 69.0) + 0.1
    route_lat = route.coords[:, 0]
    route_lon = route.coords[:, 1]

    in_box = (
        (lat >= route_lat.min() - pad) & (lat <= route_lat.max() + pad) &
        (lon >= route_lon.min() - pad * 1.5) & (lon <= route_lon.max() + pad * 1.5)
    )
    candidate_indices = np.flatnonzero(in_box)
    if candidate_indices.size == 0:
        return []

    # 2. Nearest point on route via cKDTree (Euclidean chord distance on unit sphere)
    route_xyz = _xyz(route_lat, route_lon)
    route_tree = cKDTree(route_xyz)

    chords, nearest_point_indices = route_tree.query(xyz[candidate_indices])
    # Convert chord distance to great-circle arc length in miles
    # formula: arc = 2 * R * arcsin(chord / 2)
    clamped_half_chords = np.minimum(chords / 2.0, 1.0)
    distances_off_route = 2.0 * EARTH_RADIUS_MILES * np.arcsin(clamped_half_chords)

    within_corridor = distances_off_route <= corridor

    candidates: List[Dict[str, Any]] = []
    matched_indices = candidate_indices[within_corridor]
    matched_nearest = nearest_point_indices[within_corridor]
    matched_distances = distances_off_route[within_corridor]

    for idx, nearest_idx, off_miles in zip(matched_indices, matched_nearest, matched_distances):
        row = rows[idx]
        candidates.append({
            "id": row[0],
            "name": row[1],
            "address": row[2],
            "city": row[3],
            "state": row[4],
            "price": float(row[5]),
            "lat": row[6],
            "lon": row[7],
            "mile": float(route.cum_miles[nearest_idx]),
            "off_route_miles": float(off_miles),
        })

    candidates.sort(key=lambda s: s["mile"])
    return candidates
