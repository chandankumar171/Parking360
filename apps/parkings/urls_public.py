from django.urls import path

from . import views_public as v

app_name = "parkings"

urlpatterns = [
    path("search/", v.search, name="search"),
    path("parking/<slug:slug>/", v.detail, name="detail"),
    path("api/suggest/", v.suggest, name="suggest"),
    path("api/parking/<int:pk>/quote/", v.quote, name="quote"),
]
