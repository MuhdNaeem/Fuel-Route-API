"""
API Views for the Fuel Route API.
Provides route planning endpoints and system health verification.
"""

import logging
from django.core.cache import cache
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStation
from .serializers import (
    ErrorResponseSerializer,
    RouteRequestSerializer,
    RouteResponseSerializer,
)
from .services import ors
from .services.route_planner import RoutePlanningService

logger = logging.getLogger(__name__)


class FuelRouteView(APIView):
    """
    Compute driving route and cost-optimal fuel stops across the contiguous USA.
    Accepts place names, full addresses, or 'lat,lng' coordinate strings.
    """

    @extend_schema(
        summary="Plan Route & Optimal Fuel Stops (GET)",
        description=(
            "Compute driving route and cost-optimal fuel stops using query parameters. "
            "Example: `/api/route/?start=Los+Angeles,+CA&finish=New+York,+NY`"
        ),
        parameters=[
            OpenApiParameter(
                name="start",
                description="Starting US location (address, city/state, or 'lat,lng').",
                required=True,
                type=str,
            ),
            OpenApiParameter(
                name="finish",
                description="Destination US location (address, city/state, or 'lat,lng').",
                required=True,
                type=str,
            ),
        ],
        responses={
            200: RouteResponseSerializer,
            400: ErrorResponseSerializer,
            422: ErrorResponseSerializer,
            502: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        return self._handle(request.query_params)

    @extend_schema(
        summary="Plan Route & Optimal Fuel Stops (POST)",
        description=(
            "Compute driving route and cost-optimal fuel stops using a JSON body. "
            "Example: `{\"start\": \"Chicago, IL\", \"finish\": \"Denver, CO\"}`"
        ),
        request=RouteRequestSerializer,
        responses={
            200: RouteResponseSerializer,
            400: ErrorResponseSerializer,
            422: ErrorResponseSerializer,
            502: ErrorResponseSerializer,
        },
    )
    def post(self, request):
        return self._handle(request.data)

    def _handle(self, data):
        serializer = RouteRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        start = serializer.validated_data["start"]
        finish = serializer.validated_data["finish"]

        result = RoutePlanningService.plan(start, finish)
        return Response(result, status=status.HTTP_200_OK)


class HealthCheckView(APIView):
    """
    Service health check endpoint for monitoring, Docker, and container orchestrators.
    Verifies database connectivity, station data availability, and cache responsiveness.
    """

    @extend_schema(
        summary="Service Health Check",
        description="Verify service availability, database connectivity, and station indexing.",
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "example": "healthy"},
                    "database": {"type": "string", "example": "connected"},
                    "stations_indexed": {"type": "integer", "example": 6620},
                    "cache": {"type": "string", "example": "operational"},
                },
            },
            503: ErrorResponseSerializer,
        },
    )
    def get(self, request):
        health_data = {
            "status": "healthy",
            "database": "connected",
            "stations_indexed": 0,
            "cache": "operational",
        }

        # 1. Database check
        try:
            station_count = FuelStation.objects.count()
            health_data["stations_indexed"] = station_count
            if station_count == 0:
                health_data["database"] = "empty (run load_fuel_stations)"
        except Exception as e:
            logger.error("Health check database failure: %s", e)
            return Response(
                {"status": "unhealthy", "error": f"Database unavailable: {str(e)}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # 2. Cache check
        try:
            test_key = "__health_probe__"
            cache.set(test_key, 1, timeout=10)
            if cache.get(test_key) != 1:
                health_data["cache"] = "degraded"
        except Exception as e:
            logger.warning("Health check cache probe warning: %s", e)
            health_data["cache"] = "unavailable"

        return Response(health_data, status=status.HTTP_200_OK)
