from django.urls import path
from .views import FuelRouteView, HealthCheckView

urlpatterns = [
    path("route/", FuelRouteView.as_view(), name="fuel-route"),
    path("health/", HealthCheckView.as_view(), name="health-check"),
]
