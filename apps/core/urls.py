from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.LandingView.as_view(), name="landing"),
    path("owner/", views.OwnerHomeView.as_view(), name="owner_home"),
    path("driver/", views.DriverHomeView.as_view(), name="driver_home"),
    path("superadmin/", views.SuperadminHomeView.as_view(), name="superadmin_home"),
    path("superadmin/settings/", views.platform_settings, name="platform_settings"),
    path("superadmin/audit/", views.audit_log, name="audit_log"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/read-all/", views.notifications_read_all, name="notifications_read_all"),
    path("notifications/<int:pk>/go/", views.notification_go, name="notification_go"),
    path("terms/", views.LegalView.as_view(template_name="legal/terms.html"), name="terms"),
    path("privacy/", views.LegalView.as_view(template_name="legal/privacy.html"), name="privacy"),
    path("jobs/run/", views.run_jobs, name="run_jobs"),
    path("healthz/", views.healthz, name="healthz"),
]
