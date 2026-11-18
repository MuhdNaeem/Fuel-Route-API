"""
Service layer orchestrator for end-to-end fuel routing.

Separates business logic from the HTTP controller layer:
1. Coordinates geocoding & directions (ors.py).
2. Performs spatial indexing & candidate station selection (stations.py).
3. Executes cost optimization for refueling stops (optimizer.py).
4. Assembles, thins, and structures the final response payload.
"""

import logging
import time
from typing import Any, Dict, List

import numpy as np
from django.conf import settings

from . import ors, stations
from .optimizer import plan_fuel_stops

logger = logging.getLogger(__name__)


def thin_geometry(route, step_miles: float = 0.5) -> List[List[float]]:
    """
    Downsample route coordinates to keep the response JSON lightweight and performant.
    Preserves endpoints and preserves points approximately every `step_miles`.
    """
    step = step_miles or getattr(settings, "ROUTE_GEOMETRY_STEP_MILES", 0.5)
    cum = route.cum_miles
    if len(cum) <= 2:
        return [[round(float(lon), 5), round(float(lat), 5)] for lat, lon in route.coords]

    sampled_indices = np.unique(
        np.concatenate([
            [0],
            np.searchsorted(cum, np.arange(step, cum[-1], step)),
            [len(cum) - 1],
        ])
    )
    return [
        [round(float(lon), 5), round(float(lat), 5)]
        for lat, lon in route.coords[sampled_indices]
    ]


def format_place(loc) -> Dict[str, Any]:
    """Format Location object into API response format."""
    return {
        "label": loc.label,
        "lat": round(loc.lat, 5),
        "lon": round(loc.lon, 5),
    }


class RoutePlanningService:
    """
    Coordinates end-to-end route planning and fuel stop optimization.
    """

    @classmethod
    def plan(cls, start_location: str, finish_location: str) -> Dict[str, Any]:
        """
        Execute full route planning pipeline.
        :param start_location: Address, place name, or 'lat,lng' string.
        :param finish_location: Address, place name, or 'lat,lng' string.
        :return: Fully structured result dictionary.
        """
        start_time = time.perf_counter()
        ors.external_calls.set(0)

        # 1. Geocode endpoints and compute road route
        start, finish, route = ors.plan_route(start_location, finish_location)

        # 2. Extract fuel stations within corridor of driving route
        candidates = stations.stations_along_route(route)

        # 3. Optimize fuel stops
        plan = plan_fuel_stops(
            candidates,
            route.distance_miles,
            range_miles=getattr(settings, "VEHICLE_RANGE_MILES", 500.0),
            mpg=getattr(settings, "VEHICLE_MPG", 10.0),
            fallback_price=candidates[0]["price"] if candidates else None,
        )

        # 4. Shape fuel stops list
        stops = [
            {
                "order": idx,
                "name": s["name"],
                "address": s["address"],
                "city": s["city"],
                "state": s["state"],
                "lat": round(s["lat"], 5),
                "lon": round(s["lon"], 5),
                "mile_marker": round(s["mile"], 1),
                "off_route_miles": round(s["off_route_miles"], 2),
                "price_per_gallon": round(s["price"], 3),
                "gallons_purchased": round(s["gallons"], 2),
                "cost": round(s["cost"], 2),
            }
            for idx, s in enumerate(plan["stops"], start=1)
        ]

        tank = plan["start_tank"]
        processing_ms = round((time.perf_counter() - start_time) * 1000)

        logger.info(
            "Route planned successfully: %s -> %s (Distance: %.1f mi, Stops: %d, Cost: $%.2f, Time: %d ms)",
            start.label,
            finish.label,
            route.distance_miles,
            len(stops),
            plan["total_cost"],
            processing_ms,
        )

        return {
            "start": format_place(start),
            "finish": format_place(finish),
            "route": {
                "distance_miles": round(route.distance_miles, 1),
                "duration_hours": round(route.duration_seconds / 3600.0, 2),
                "geometry": {
                    "type": "LineString",
                    "coordinates": thin_geometry(route),
                },
            },
            "fuel_stops": stops,
            "starting_tank": {
                "note": "Full tank at departure, valued at the first stop's price.",
                "gallons": round(tank["gallons"], 2),
                "price_per_gallon": round(tank["price"], 3),
                "cost": round(tank["cost"], 2),
            },
            "summary": {
                "total_fuel_cost_usd": round(plan["total_cost"], 2),
                "total_gallons": round(plan["total_gallons"], 2),
                "number_of_fuel_stops": len(stops),
                "assumptions": {
                    "mpg": getattr(settings, "VEHICLE_MPG", 10.0),
                    "max_range_miles": getattr(settings, "VEHICLE_RANGE_MILES", 500.0),
                },
            },
            "meta": {
                "external_api_calls": ors.external_calls.get(),
                "candidate_stations_considered": len(candidates),
                "processing_ms": processing_ms,
            },
        }
