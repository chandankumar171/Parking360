from django.contrib import admin

from .models import Booking


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "booking_code", "parking", "driver", "vehicle_number_plate", "start_at", "status", "base_amount")
    list_filter = ("status",)
    search_fields = ("booking_code", "vehicle_number_plate", "driver__email", "parking__name")
    date_hierarchy = "start_at"
