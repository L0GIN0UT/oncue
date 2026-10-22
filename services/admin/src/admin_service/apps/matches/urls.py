"""Маршруты раздела «Матчи» (конвертер ``roomcode`` — ``core.converters``)."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.matches import views

app_name = "matches"

urlpatterns = [
    path("", views.room_list, name="rooms"),
    path("rooms/<roomcode:code>/abort/", views.room_abort, name="abort"),
    path("lookup/", views.match_lookup, name="lookup"),
    path("<uuid:match_id>/", views.match_detail, name="match"),
]
