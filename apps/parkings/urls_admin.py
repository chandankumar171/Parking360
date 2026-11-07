from django.urls import path

from apps.accounts import views_admin as av
from apps.bookings import views_admin as bkv
from apps.payments import views_admin as pv

from . import views_admin as v

app_name = "sadmin"

urlpatterns = [
    path("owners/", av.owners, name="owners"),
    path("drivers/", av.drivers, name="drivers"),
    path("users/<int:pk>/", av.user_detail, name="user_detail"),
    path("users/<int:pk>/toggle/", av.user_toggle, name="user_toggle"),
    path("bookings/", bkv.bookings_list, name="bookings"),
    path("bookings/<int:pk>/", bkv.booking_detail, name="booking_detail"),
    path("payments/", pv.payments_list, name="payments"),
    path("payouts/", pv.payouts, name="payouts"),
    path("payouts/record/", pv.record_payout, name="record_payout"),
    path("verification/", v.queue, name="queue"),
    path("parkings/", v.parking_list, name="parking_list"),
    path("parkings/<int:pk>/", v.parking_detail, name="parking_detail"),
    path("parkings/<int:pk>/approve/", v.approve, name="approve"),
    path("parkings/<int:pk>/reject/", v.reject, name="reject"),
    path("parkings/<int:pk>/suspend/", v.suspend, name="suspend"),
    path("parkings/<int:pk>/unsuspend/", v.unsuspend, name="unsuspend"),
]
