from django.urls import path

from . import views

app_name = "bookings"

urlpatterns = [
    path("bookings/", views.my_bookings, name="list"),
    path("bookings/<int:pk>/", views.booking_detail, name="detail"),
    path("bookings/<int:pk>/release/", views.release, name="release"),
    path("bookings/<int:pk>/refund/", views.request_refund, name="request_refund"),
]
