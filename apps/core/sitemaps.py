from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return ["core:landing", "parkings:search", "accounts:login", "accounts:signup", "core:terms", "core:privacy"]

    def location(self, item):
        return reverse(item)


class ParkingSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.7

    def items(self):
        from apps.parkings.models import Parking

        return Parking.objects.filter(status=Parking.Status.APPROVED).order_by("-updated_at")[:5000]

    def lastmod(self, obj):
        return obj.updated_at
