"""
Centralized exception handling for the routing API.
Ensures consistent, structured JSON responses across all failure modes.
"""

import logging
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .services.errors import RoutingError

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    """
    Standardized DRF exception handler.
    Guarantees that all API errors return a consistent JSON payload:
    {"error": "<human readable message>", "details": ...}
    """
    # 1. Handle domain-specific business and routing errors
    if isinstance(exc, RoutingError):
        logger.warning(
            "Routing error: %s (status=%d, view=%s)",
            str(exc),
            exc.status,
            context.get("view").__class__.__name__ if context.get("view") else "Unknown",
        )
        return Response({"error": str(exc)}, status=exc.status)

    # 2. Delegate standard DRF exceptions (ValidationError, ParseError, AuthenticationFailed, etc.)
    response = drf_exception_handler(exc, context)

    if response is not None:
        first_msg = "Invalid request."
        if isinstance(response.data, dict):
            # Extract first error for uniform top-level error string
            for key, val in response.data.items():
                if isinstance(val, (list, tuple)) and val:
                    first_msg = f"{key}: {val[0]}"
                elif isinstance(val, str):
                    first_msg = f"{key}: {val}"
                break
            response.data = {
                "error": first_msg,
                "details": response.data,
            }
        elif isinstance(response.data, list) and response.data:
            first_msg = str(response.data[0])
            response.data = {
                "error": first_msg,
                "details": response.data,
            }
        logger.info("Client error (%d): %s", response.status_code, first_msg)
        return response

    # 3. Unhandled server exceptions (500)
    logger.exception("Unhandled exception in API request: %s", str(exc))
    return Response(
        {"error": "An unexpected server error occurred. Please try again later."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
