from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.core.sitemaps import ParkingSitemap, StaticViewSitemap
from apps.core.views import robots_txt

handler400 = "apps.core.views.error_400"
handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("owner/", include("apps.parkings.urls_owner")),
    path("superadmin/", include("apps.parkings.urls_admin")),
    path("", include("apps.parkings.urls_public")),
    path("", include("apps.bookings.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.reviews.urls")),
    path("", include("apps.dashboard.urls")),
    path("sitemap.xml", sitemap, {"sitemaps": {"static": StaticViewSitemap, "parkings": ParkingSitemap}}, name="sitemap"),
    path("robots.txt", robots_txt, name="robots"),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
