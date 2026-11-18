"""
Unit tests for the fuel stop optimization algorithm.
Validates the greedy gas station solver against edge cases, boundary conditions, and invariants.
"""

from django.test import SimpleTestCase

from routing.services.errors import NoFeasibleStops
from routing.services.optimizer import plan_fuel_stops


def st(mile: float, price: float, name: str = "TestStation"):
    """Helper creating a minimal station candidate dictionary."""
    return {"mile": float(mile), "price": float(price), "name": name}


class OptimizerTests(SimpleTestCase):
    def test_short_trip_needs_no_stop(self):
        """Trip under 500 miles needs 0 stops; origin tank priced at fallback price."""
        r = plan_fuel_stops([st(100, 3.0)], 300, fallback_price=3.0)
        self.assertEqual(r["stops"], [])
        self.assertAlmostEqual(r["total_cost"], 30.0 * 3.0)
        self.assertAlmostEqual(r["total_gallons"], 30.0)

    def test_gallons_always_equal_distance_over_mpg(self):
        """Invariant: Total gallons consumed must always equal total_miles / MPG."""
        stations = [st(m, 3.0 + (m % 7) / 10.0) for m in range(50, 1500, 60)]
        r = plan_fuel_stops(stations, 1400.0, range_miles=500.0, mpg=10.0)
        self.assertAlmostEqual(r["total_gallons"], 140.0)

    def test_no_leg_exceeds_range(self):
        """Invariant: Distance between consecutive stops (and to endpoints) <= range_miles."""
        stations = [st(m, 3.0 + (m % 11) / 10.0) for m in range(40, 2000, 40)]
        r = plan_fuel_stops(stations, 1900.0, range_miles=500.0)
        markers = [0.0] + [s["mile"] for s in r["stops"]] + [1900.0]
        for a, b in zip(markers, markers[1:]):
            self.assertLessEqual(b - a, 500.0)

    def test_prefers_cheap_station_over_nearer_expensive_one(self):
        """Skip expensive station in range to fill up entirely at cheaper station ahead."""
        # A (mile 100, $4) vs B (mile 480, $3): entire refuel occurs at B
        r = plan_fuel_stops([st(100, 4.0), st(480, 3.0)], 900.0)
        self.assertEqual([s["mile"] for s in r["stops"]], [480.0])
        self.assertAlmostEqual(r["stops"][0]["gallons"], 40.0)  # 400 miles needed to reach 900

    def test_buys_only_enough_to_reach_cheaper_station(self):
        """Buy minimal fuel needed at expensive station to reach downstream cheaper station."""
        # A (mile 450, $3.5) then cheaper B (mile 600, $3.0): at A buy 10 gal (100 miles), fill at B
        r = plan_fuel_stops([st(450, 3.5), st(600, 3.0)], 1000.0)
        a, b = r["stops"]
        self.assertAlmostEqual(a["gallons"], 10.0)
        self.assertAlmostEqual(b["gallons"], 40.0)

    def test_gap_larger_than_range_raises(self):
        """Raise NoFeasibleStops when distance between consecutive stations exceeds range."""
        with self.assertRaises(NoFeasibleStops):
            plan_fuel_stops([st(100, 3.0), st(800, 3.0)], 1200.0)

    def test_missing_fallback_price_raises_when_no_stops(self):
        """When no stops are made and no fallback price is available, raise NoFeasibleStops."""
        with self.assertRaises(NoFeasibleStops):
            plan_fuel_stops([], 300.0, fallback_price=None)

    def test_tie_break_selects_furthest_station_on_identical_price(self):
        """When two reachable stations share the lowest price, pick the furthest one."""
        stations = [st(300, 3.0, "StationA"), st(450, 3.0, "StationB")]
        r = plan_fuel_stops(stations, 800.0)
        self.assertEqual(len(r["stops"]), 1)
        self.assertEqual(r["stops"][0]["name"], "StationB")
        self.assertEqual(r["stops"][0]["mile"], 450.0)

    def test_exact_500_mile_trip(self):
        """A trip of exactly 500 miles fits in the starting tank with 0 stops."""
        r = plan_fuel_stops([st(250, 3.2)], 500.0, fallback_price=3.2)
        self.assertEqual(len(r["stops"]), 0)
        self.assertAlmostEqual(r["total_gallons"], 50.0)
