"""
Integration tests for the Fuel Route API endpoints.
Tests happy paths, edge cases, input validation, upstream error mapping, and health check.
"""

from unittest import mock
import numpy as np
from django.test import TestCase

from routing.models import FuelStation
from routing.services import stations
from routing.services.errors import (
    InvalidLocation,
    LocationNotInUSA,
    NoFeasibleStops,
    RouteNotFound,
    UpstreamError,
)
from routing.services.ors import Location, Route

ROUTE_URL = "/api/route/"
HEALTH_URL = "/api/health/"
SCHEMA_URL = "/api/schema/"
DOCS_URL = "/api/docs/"


def create_mock_route(distance_miles: float = 1000.0):
    """Generate a synthetic west-to-east route polyline."""
    num_pts = 400
    lon = np.linspace(-110, -88, num_pts)
    coords = np.column_stack([np.full_like(lon, 40.0), lon])
    miles = np.linspace(0, distance_miles, num_pts)
    return (
        Location(40.0, -110.0, "Start Point, NE"),
        Location(40.0, -88.0, "Finish Point, IL"),
        Route(coords, miles, float(distance_miles), float(distance_miles * 36.0)),
    )


class FuelRouteApiTests(TestCase):
    def setUp(self):
        stations.clear_cache()
        # Seed test stations along latitude 40.01 (~0.7 miles from route)
        for i, lon in enumerate([-107, -103, -99, -95, -91]):
            FuelStation.objects.create(
                opis_id=i + 1,
                name=f"Station_{i}",
                address="123 Highway Way",
                city="Testville",
                state="NE",
                price=3.00 + i * 0.10,
                latitude=40.01,
                longitude=lon,
            )
        # Far-off station: 45.0 latitude (~345 miles north: must be discarded)
        FuelStation.objects.create(
            opis_id=99,
            name="FarAwayStation",
            address="999 North Road",
            city="Northtown",
            state="SD",
            price=1.50,
            latitude=45.0,
            longitude=-100.0,
        )

    def tearDown(self):
        stations.clear_cache()

    @mock.patch("routing.views.ors.plan_route", side_effect=lambda s, f: create_mock_route(1000.0))
    def test_post_success_shape_and_totals(self, _):
        """Verify successful POST response schema, math totals, and candidate station filtering."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Los Angeles, CA", "finish": "New York, NY"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Schema structure assertions
        self.assertIn("start", data)
        self.assertIn("finish", data)
        self.assertIn("route", data)
        self.assertIn("fuel_stops", data)
        self.assertIn("starting_tank", data)
        self.assertIn("summary", data)
        self.assertIn("meta", data)

        # Route geometry
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertGreater(len(data["route"]["geometry"]["coordinates"]), 1)

        # Consumption math: total gallons == distance / 10 MPG
        expected_gallons = data["route"]["distance_miles"] / 10.0
        self.assertAlmostEqual(data["summary"]["total_gallons"], expected_gallons, places=1)

        # Spatial corridor filter: far station must NOT be selected
        stop_names = [s["name"] for s in data["fuel_stops"]]
        self.assertNotIn("FarAwayStation", stop_names)

        # Stop fields integrity
        for stop in data["fuel_stops"]:
            self.assertIn("order", stop)
            self.assertIn("name", stop)
            self.assertIn("lat", stop)
            self.assertIn("lon", stop)
            self.assertIn("mile_marker", stop)
            self.assertIn("price_per_gallon", stop)
            self.assertIn("gallons_purchased", stop)
            self.assertIn("cost", stop)
            self.assertGreater(stop["price_per_gallon"], 0)
            self.assertGreater(stop["gallons_purchased"], 0)

    @mock.patch("routing.views.ors.plan_route", side_effect=lambda s, f: create_mock_route(800.0))
    def test_get_method_with_query_params(self, _):
        """Verify GET /api/route/?start=...&finish=... returns 200 with matching schema."""
        response = self.client.get(
            f"{ROUTE_URL}?start=Chicago,+IL&finish=Denver,+CO",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertAlmostEqual(data["summary"]["total_gallons"], 80.0, places=1)

    def test_missing_field_is_400(self):
        """Missing required fields returns 400 Bad Request with standardized error message."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Chicago, IL"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("details", data)

    def test_same_start_and_finish_is_400(self):
        """Identical start and finish inputs are rejected at serializer boundary with 400."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Chicago, IL", "finish": "Chicago, IL"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)
        self.assertIn("cannot be identical", data["error"].lower())

    def test_empty_string_location_is_400(self):
        """Empty or whitespace-only locations are rejected with 400."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "   ", "finish": "Denver, CO"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    @mock.patch("routing.views.ors.plan_route", side_effect=LocationNotInUSA("Location is outside the USA."))
    def test_non_us_location_is_400(self, _):
        """Non-US locations raise LocationNotInUSA and map to 400 Bad Request."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Toronto, Canada", "finish": "Chicago, IL"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["error"], "Location is outside the USA.")

    @mock.patch("routing.views.ors.plan_route", side_effect=RouteNotFound("No drivable route found."))
    def test_route_not_found_is_422(self, _):
        """Unreachable road routes map to 422 Unprocessable Entity."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Honolulu, HI", "finish": "San Francisco, CA"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertEqual(data["error"], "No drivable route found.")

    @mock.patch("routing.views.ors.plan_route", side_effect=NoFeasibleStops("No fuel station within range."))
    def test_no_feasible_stops_is_422(self, _):
        """Route with impossible fuel gaps maps to 422 Unprocessable Entity."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Origin, USA", "finish": "Destination, USA"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertEqual(data["error"], "No fuel station within range.")

    @mock.patch("routing.views.ors.plan_route", side_effect=UpstreamError("OpenRouteService rate limit exceeded."))
    def test_upstream_error_is_502(self, _):
        """Third-party service failure or rate limit maps to 502 Bad Gateway."""
        response = self.client.post(
            ROUTE_URL,
            {"start": "Chicago, IL", "finish": "Denver, CO"},
            content_type="application/json",
            HTTP_HOST="127.0.0.1",
        )
        self.assertEqual(response.status_code, 502)
        data = response.json()
        self.assertIn("rate limit exceeded", data["error"])

    def test_health_check_endpoint(self):
        """Verify /api/health/ reports healthy service, connected DB, and station count."""
        response = self.client.get(HEALTH_URL, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "connected")
        self.assertGreater(data["stations_indexed"], 0)
        self.assertEqual(data["cache"], "operational")

    def test_openapi_schema_endpoint(self):
        """Verify OpenAPI schema generation endpoint /api/schema/ returns 200."""
        response = self.client.get(SCHEMA_URL, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)

    def test_swagger_ui_endpoint(self):
        """Verify Swagger UI endpoint /api/docs/ returns 200 HTML."""
        response = self.client.get(DOCS_URL, HTTP_HOST="127.0.0.1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
