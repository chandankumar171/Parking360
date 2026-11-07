from django.contrib import admin

from .models import ParkingReview


@admin.register(ParkingReview)
class ParkingReviewAdmin(admin.ModelAdmin):
    list_display = ("parking", "driver", "rating", "created_at")
    list_filter = ("rating",)
    search_fields = ("parking__name", "driver__email", "comment")
