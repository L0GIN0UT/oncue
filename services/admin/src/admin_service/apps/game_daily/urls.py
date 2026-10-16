"""Маршруты раздела «Наборы дня» (конвертеры ``modekey``, ``isodate`` — ``core.converters``)."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.game_daily import views

app_name = "game_daily"

urlpatterns = [
    path("", views.calendar, name="calendar"),
    path("<modekey:key>/<isodate:day>/items/<int:index>/", views.replace_item, name="item"),
    path("<modekey:key>/<isodate:day>/regenerate/", views.regenerate, name="regenerate"),
    path("<modekey:key>/<isodate:day>/theme/", views.theme, name="theme"),
]
