"""
Domain exceptions for fuel routing, geocoding, and optimization.
"""


class RoutingError(Exception):
    """Base class for all routing domain exceptions. `status` is the HTTP status code."""
    status: int = 400


class InvalidLocation(RoutingError):
    """Raised when an address or coordinate string cannot be resolved or is invalid."""
    status: int = 400


class LocationNotInUSA(InvalidLocation):
    """Raised when a location resolves outside the contiguous United States."""
    status: int = 400


class RouteNotFound(RoutingError):
    """Raised when the routing engine cannot find a drivable road between locations."""
    status: int = 422


class NoFeasibleStops(RoutingError):
    """Raised when vehicle fuel range is exceeded before reaching another station or destination."""
    status: int = 422


class UpstreamError(RoutingError):
    """Raised when external geocoding or routing services fail, time out, or rate limit."""
    status: int = 502
