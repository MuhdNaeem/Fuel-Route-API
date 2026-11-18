"""
Cost-optimal fuel stops for a vehicle trip with a limited tank range.

Pure function (zero Django / HTTP dependencies) for testability and performance.

Greedy rule, optimal for the classic "Gas Station Refueling" dynamic:
  At each stop, inspect every candidate station reachable on a full tank:
    1. If destination is reachable:
       -> Buy only the fuel needed to reach the destination, then complete route.
    2. Elif a CHEAPER station is reachable:
       -> Buy just enough fuel to reach the nearest cheaper station.
    3. Else (current station is the cheapest in range):
       -> Fill the tank completely, then advance to the cheapest station in range.
  In this manner, fuel is only ever purchased at a price <= subsequent stops.

Assumptions:
- The vehicle departs origin with a full tank (500-mile range).
- Origin price data is unavailable, so the departure tank is valued at the first stop's
  price (or `fallback_price` if no intermediate stop is required).
- The vehicle arrives at the destination with an empty tank, ensuring:
  `total_gallons == total_distance_miles / mpg`.
"""

from typing import Any, Dict, List, Optional
from .errors import NoFeasibleStops


def plan_fuel_stops(
    stations: List[Dict[str, Any]],
    total_miles: float,
    range_miles: float = 500.0,
    mpg: float = 10.0,
    fallback_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute optimal fuel stops along a road route.

    :param stations: List of station dictionaries containing at least 'mile' and 'price', sorted by mile.
    :param total_miles: Total driving route length in miles.
    :param range_miles: Maximum vehicle distance on a full tank (default 500.0).
    :param mpg: Vehicle fuel economy in miles per gallon (default 10.0).
    :param fallback_price: Unit fuel price used to value origin tank when no stops are needed.
    :return: Dictionary containing 'stops', 'start_tank', 'total_gallons', and 'total_cost'.
    """
    stops: List[Dict[str, Any]] = []
    pos, fuel = 0.0, float(range_miles)  # Departs origin with a full tank
    cur, price_here = -1, 0.0           # -1 denotes current position at the origin

    while True:
        remaining = total_miles - pos
        if remaining <= range_miles:
            # Destination fits in remaining tank capacity
            if stops:
                _buy(stops[-1], max(0.0, remaining - fuel), mpg)
            break

        # Identify reachable stations ahead on a full tank
        ahead = [
            (j, s) for j, s in enumerate(stations)
            if j > cur and pos < s["mile"] <= pos + range_miles
        ]
        if not ahead:
            raise NoFeasibleStops(
                f"No fuel station within {range_miles:.0f} miles after mile {pos:.0f}."
            )

        cheaper = next(((j, s) for j, s in ahead if s["price"] < price_here), None)
        if cur == -1 or not cheaper:
            # Origin tank was pre-filled, or this is the local minimum price in range:
            # Target the cheapest station in range (tie-break with furthest station), filling up here first.
            j, s = min(ahead, key=lambda t: (t[1]["price"], -t[1]["mile"]))
            buy = 0.0 if cur == -1 else range_miles - fuel
        else:
            j, s = cheaper  # Buy only enough fuel to reach this cheaper station
            buy = max(0.0, (s["mile"] - pos) - fuel)

        if stops:
            _buy(stops[-1], buy, mpg)

        fuel += buy - (s["mile"] - pos)
        pos, cur, price_here = s["mile"], j, s["price"]
        stops.append(_open_stop(s, fuel))

    # Value the departure tank (fuel utilized during the initial leg)
    start_miles = min(range_miles, total_miles)
    start_price = stops[0]["price"] if stops else fallback_price
    if start_price is None:
        raise NoFeasibleStops("No fuel price data available near this route.")

    start_gal = start_miles / mpg
    start_tank = {
        "gallons": start_gal,
        "price": start_price,
        "cost": start_gal * start_price,
    }
    total_cost = start_tank["cost"] + sum(s["cost"] for s in stops)
    total_gallons = start_gal + sum(s["gallons"] for s in stops)

    return {
        "stops": stops,
        "start_tank": start_tank,
        "total_gallons": total_gallons,
        "total_cost": total_cost,
    }


def _open_stop(station: Dict[str, Any], fuel_before: float) -> Dict[str, Any]:
    s = dict(station)
    s.update(fuel_before_miles=fuel_before, gallons=0.0, cost=0.0)
    return s


def _buy(stop: Dict[str, Any], miles_of_fuel: float, mpg: float) -> None:
    gallons = miles_of_fuel / mpg
    stop["gallons"] += gallons
    stop["cost"] += gallons * stop["price"]
