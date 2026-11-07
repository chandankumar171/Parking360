from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("payments/booking/<int:pk>/order/", views.booking_order, name="booking_order"),
    path("payments/overtime/<int:pk>/order/", views.overtime_order, name="overtime_order"),
    path("payments/verify/", views.verify, name="verify"),
    path("payments/webhook/", views.webhook, name="webhook"),
]
