"""
Request and Response Serializers for the Fuel Route API.
Provides validation and OpenAPI schema specifications.
"""

from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    """
    Request payload for route planning and fuel stop calculation.
    Supports US city/state names, street addresses, or 'lat,lng' coordinate strings.
    """
    start = serializers.CharField(
        max_length=200,
        min_length=1,
        trim_whitespace=True,
        required=True,
        help_text="Starting origin (e.g. 'Los Angeles, CA' or '34.05,-118.24').",
    )
    finish = serializers.CharField(
        max_length=200,
        min_length=1,
        trim_whitespace=True,
        required=True,
        help_text="Destination finish point (e.g. 'New York, NY' or '40.71,-74.00').",
    )

    def validate(self, attrs):
        start = attrs.get("start", "").strip()
        finish = attrs.get("finish", "").strip()

        if not start:
            raise serializers.ValidationError({"start": "Starting location cannot be empty."})
        if not finish:
            raise serializers.ValidationError({"finish": "Destination location cannot be empty."})
        if start.lower() == finish.lower():
            raise serializers.ValidationError(
                {"finish": "Start and finish cannot be identical locations."}
            )

        attrs["start"] = start
        attrs["finish"] = finish
        return attrs


# --- Response Documentation Serializers (for OpenAPI / Swagger UI) ---

class PlaceSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="Human-readable place label or coordinates.")
    lat = serializers.FloatField(help_text="Latitude in decimal degrees.")
    lon = serializers.FloatField(help_text="Longitude in decimal degrees.")


class GeometrySerializer(serializers.Serializer):
    type = serializers.CharField(default="LineString")
    coordinates = serializers.ListField(
        child=serializers.ListField(child=serializers.FloatField(), min_length=2, max_length=2),
        help_text="Array of [lon, lat] coordinates representing the route path.",
    )


class RouteDetailSerializer(serializers.Serializer):
    distance_miles = serializers.FloatField(help_text="Total driving distance in miles.")
    duration_hours = serializers.FloatField(help_text="Estimated driving time in hours.")
    geometry = GeometrySerializer()


class FuelStopSerializer(serializers.Serializer):
    order = serializers.IntegerField(help_text="Sequential stop sequence index.")
    name = serializers.CharField(help_text="Fuel station brand or truckstop name.")
    address = serializers.CharField(help_text="Street address of the station.")
    city = serializers.CharField(help_text="City name.")
    state = serializers.CharField(help_text="2-letter US state postal abbreviation.")
    lat = serializers.FloatField(help_text="Station latitude.")
    lon = serializers.FloatField(help_text="Station longitude.")
    mile_marker = serializers.FloatField(help_text="Route mileage where stop occurs.")
    off_route_miles = serializers.FloatField(help_text="Detour distance from route centerline.")
    price_per_gallon = serializers.FloatField(help_text="Retail fuel price per gallon (USD).")
    gallons_purchased = serializers.FloatField(help_text="Gallons of fuel purchased at this stop.")
    cost = serializers.FloatField(help_text="Total cost of fuel purchased at this stop (USD).")


class StartingTankSerializer(serializers.Serializer):
    note = serializers.CharField(help_text="Explanation of origin tank valuation.")
    gallons = serializers.FloatField(help_text="Initial fuel volume burned before first stop.")
    price_per_gallon = serializers.FloatField(help_text="Price used to value origin tank (USD).")
    cost = serializers.FloatField(help_text="Total cost allocated to departure fuel (USD).")


class SummaryAssumptionsSerializer(serializers.Serializer):
    mpg = serializers.FloatField(help_text="Vehicle fuel economy.")
    max_range_miles = serializers.FloatField(help_text="Maximum vehicle range on full tank.")


class SummarySerializer(serializers.Serializer):
    total_fuel_cost_usd = serializers.FloatField(help_text="Total estimated fuel expenditure in USD.")
    total_gallons = serializers.FloatField(help_text="Total gallons consumed for entire trip.")
    number_of_fuel_stops = serializers.IntegerField(help_text="Total number of refueling stops.")
    assumptions = SummaryAssumptionsSerializer()


class MetaSerializer(serializers.Serializer):
    external_api_calls = serializers.IntegerField(help_text="Number of external ORS calls executed.")
    candidate_stations_considered = serializers.IntegerField(help_text="Stations within corridor evaluated.")
    processing_ms = serializers.IntegerField(help_text="Server-side processing duration in milliseconds.")


class RouteResponseSerializer(serializers.Serializer):
    start = PlaceSerializer()
    finish = PlaceSerializer()
    route = RouteDetailSerializer()
    fuel_stops = serializers.ListField(child=FuelStopSerializer())
    starting_tank = StartingTankSerializer()
    summary = SummarySerializer()
    meta = MetaSerializer()


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField(help_text="Description of the error.")
    details = serializers.DictField(required=False, help_text="Field-level validation details.")
