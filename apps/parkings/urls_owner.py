from django.urls import path

from apps.bookings import views_owner as bv
from apps.reviews import views as rv

from . import views_owner as v

app_name = "owner"

urlpatterns = [
    path("reviews/", rv.owner_reviews, name="reviews"),
    path("reviews/<int:pk>/reply/", rv.reply, name="review_reply"),
    path("drivers/<int:pk>/", rv.driver_profile, name="driver_profile"),
    path("bookings/", bv.bookings_list, name="bookings"),
    path("bookings/<int:pk>/", bv.booking_detail, name="booking_detail"),
    path("bookings/<int:pk>/status/", bv.booking_status, name="booking_status"),
    path("bookings/<int:pk>/check-in/", bv.check_in, name="check_in"),
    path("bookings/<int:pk>/checkout/start/", bv.checkout_start, name="checkout_start"),
    path("bookings/<int:pk>/checkout/complete/", bv.checkout_complete, name="checkout_complete"),
    path("bookings/<int:pk>/cancel/", bv.cancel_booking, name="cancel_booking"),
    path("verify/", bv.verify_code, name="verify"),
    path("refunds/", bv.refunds_list, name="refunds"),
    path("refunds/<int:pk>/approve/", bv.refund_approve, name="refund_approve"),
    path("refunds/<int:pk>/reject/", bv.refund_reject, name="refund_reject"),
    path("earnings/", bv.earnings, name="earnings"),
    path("parkings/", v.parking_list, name="parking_list"),
    path("parkings/new/", v.parking_create, name="parking_create"),
    path("parkings/<int:pk>/edit/", v.parking_edit, name="parking_edit_default"),
    path("parkings/<int:pk>/edit/<slug:step>/", v.parking_edit, name="parking_edit"),
    path("parkings/<int:pk>/submit/", v.parking_submit, name="parking_submit"),
    path("parkings/<int:pk>/toggle-open/", v.parking_toggle_open, name="parking_toggle_open"),
    path("parkings/<int:pk>/delete/", v.parking_delete, name="parking_delete"),
    path("parkings/<int:pk>/photos/<int:photo_pk>/cover/", v.photo_cover, name="photo_cover"),
    path("parkings/<int:pk>/photos/<int:photo_pk>/delete/", v.photo_delete, name="photo_delete"),
]
