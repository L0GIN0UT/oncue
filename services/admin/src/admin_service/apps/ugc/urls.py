"""Маршруты раздела «UGC-изображения»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.ugc import views

app_name = "ugc"

urlpatterns = [
    path("", views.queue, name="queue"),
    path("<uuid:asset_id>/", views.card, name="card"),
    path("<uuid:asset_id>/approve/", views.approve, name="approve"),
    path("<uuid:asset_id>/reject/", views.reject, name="reject"),
]
