from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("owner/dashboard/api/overview/", views.owner_overview, name="owner_overview"),
    path("owner/dashboard/api/live/", views.owner_live, name="owner_live"),
    path("owner/dashboard/export/<slug:kind>/", views.owner_export, name="owner_export"),
    path("superadmin/dashboard/api/overview/", views.admin_overview, name="admin_overview"),
    path("superadmin/dashboard/export/<slug:kind>/", views.admin_export, name="admin_export"),
]
