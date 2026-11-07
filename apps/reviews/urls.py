from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("owners/<int:pk>/", views.owner_profile, name="owner_profile"),
    path("bookings/<int:pk>/review/", views.write_review, name="write"),
]
