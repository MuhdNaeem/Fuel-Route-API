"""
Unit tests for routing services:
- OpenRouteService client (ors.py)
- Spatial KD-tree station indexing (stations.py)
- Polyline thinning and place formatting (route_planner.py)
"""

from unittest import mock
import numpy as np
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from routing.models import FuelStation
from routing.services import ors, stations
from routing.services.errors import InvalidLocation, LocationNotInUSA, UpstreamError
from routing.services.ors import Location, Route, geocode, haversine_miles
from routing.services.route_planner import format_place, thin_geometry


class OrsClientTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_geocode_valid_coordinates(self):
        """Coordinate strings within contiguous US are parsed without external HTTP calls."""
        loc = geocode("34.0522,-118.2437")
        self.assertAlmostEqual(loc.lat, 34.0522)
        self.assertAlmostEqual(loc.lon, -118.2437)

    def test_geocode_coordinates_outside_usa(self):
        """Coordinates outside the contiguous US raise LocationNotInUSA."""
        with self.assertRaises(LocationNotInUSA):
            geocode("51.5074,-0.1278")  # London, UK

    def test_geocode_invalid_coordinate_ranges(self):
        """Coordinates with latitude > 90 or longitude > 180 raise InvalidLocation."""
        with self.assertRaises(InvalidLocation):
            geocode("195.0,200.0")

    def test_geocode_empty_string(self):
        """Empty or whitespace-only location strings raise InvalidLocation."""
        with self.assertRaises(InvalidLocation):
            geocode("   ")

    def test_haversine_distance_calculation(self):
        """Haversine math calculates accurate great-circle distances."""
        # 1 degree of latitude is approximately 69 miles
        d = haversine_miles(np.array([40.0]), np.array([-100.0]), np.array([41.0]), np.array([-100.0]))
        self.assertAlmostEqual(float(d[0]), 69.09, places=1)

    @mock.patch("routing.services.ors._http")
    def test_ors_rate_limit_429_raises_upstream_error(self, mock_http):
        """HTTP 429 response from ORS raises UpstreamError with rate limit message."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 429
        mock_http.return_value = mock_resp

        with self.assertRaises(UpstreamError) as ctx:
            geocode("Chicago, IL")
        self.assertIn("rate limit", str(ctx.exception).lower())

    @mock.patch("routing.services.ors._http")
    def test_ors_auth_failure_401_raises_upstream_error(self, mock_http):
        """HTTP 401 response from ORS raises UpstreamError with authentication message."""
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 401
        mock_http.return_value = mock_resp

        with self.assertRaises(UpstreamError) as ctx:
            geocode("New York, NY")
        self.assertIn("authentication", str(ctx.exception).lower())


class SpatialStationsTests(TestCase):
    def setUp(self):
        stations.clear_cache()
        # Create station 1 mile off route
        FuelStation.objects.create(
            opis_id=101,
            name="NearStation",
            address="Near Ave",
            city="Ogallala",
            state="NE",
            price=3.10,
            latitude=40.01,
            longitude=-100.0,
        )
        # Create station 15 miles off route (outside 5-mile corridor)
        FuelStation.objects.create(
            opis_id=102,
            name="OffRouteStation",
            address="Far Ave",
            city="FarCity",
            state="NE",
            price=2.50,
            latitude=40.25,
            longitude=-100.0,
        )

    def tearDown(self):
        stations.clear_cache()

    def test_stations_along_route_filters_by_corridor(self):
        """Only stations within CORRIDOR_MILES (5 miles) of route coordinates are returned."""
        # Simple east-west line along lat 40.0
        coords = np.column_stack([np.full(100, 40.0), np.linspace(-105.0, -95.0, 100)])
        cum_miles = np.linspace(0.0, 500.0, 100)
        route = Route(coords, cum_miles, 500.0, 18000.0)

        candidates = stations.stations_along_route(route, corridor_miles=5.0)
        names = [c["name"] for c in candidates]
        self.assertIn("NearStation", names)
        self.assertNotIn("OffRouteStation", names)

    def test_empty_database_returns_empty_candidates(self):
        """Empty station database returns empty candidate list gracefully."""
        FuelStation.objects.all().delete()
        stations.clear_cache()

        coords = np.column_stack([np.full(10, 40.0), np.linspace(-105.0, -95.0, 10)])
        cum_miles = np.linspace(0.0, 100.0, 10)
        route = Route(coords, cum_miles, 100.0, 3600.0)

        candidates = stations.stations_along_route(route)
        self.assertEqual(candidates, [])


class RoutePlannerGeometryTests(SimpleTestCase):
    def test_thin_geometry_preserves_start_and_end(self):
        """Polyline thinning preserves exact start and finish coordinates."""
        coords = np.column_stack([np.linspace(34.0, 40.0, 1000), np.linspace(-118.0, -74.0, 1000)])
        cum_miles = np.linspace(0.0, 2800.0, 1000)
        route = Route(coords, cum_miles, 2800.0, 100000.0)

        thinned = thin_geometry(route, step_miles=5.0)
        # Check start point (lon, lat)
        self.assertEqual(thinned[0], [round(float(coords[0, 1]), 5), round(float(coords[0, 0]), 5)])
        # Check finish point (lon, lat)
        self.assertEqual(thinned[-1], [round(float(coords[-1, 1]), 5), round(float(coords[-1, 0]), 5)])
        # Must be drastically smaller than 1000 points
        self.assertLess(len(thinned), 600)

    def test_format_place(self):
        """Location object is formatted with label and rounded coordinates."""
        loc = Location(34.052234, -118.243685, "Los Angeles, CA")
        place = format_place(loc)
        self.assertEqual(place["label"], "Los Angeles, CA")
        self.assertEqual(place["lat"], 34.05223)
        self.assertEqual(place["lon"], -118.24368)
