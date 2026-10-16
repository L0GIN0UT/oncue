"""Маршруты раздела «Режимы» (конвертер ``modekey`` — ``core.converters``)."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.game_config import views

app_name = "game_config"

urlpatterns = [
    path("", views.mode_list, name="modes"),
    path("new/", views.mode_new, name="new"),
    path("<modekey:key>/", views.mode_detail, name="mode"),
    path("<modekey:key>/edit/", views.mode_edit, name="edit"),
    path("<modekey:key>/publish/", views.mode_publish, name="publish"),
    path("<modekey:key>/rollback/", views.mode_rollback, name="rollback"),
    path("<modekey:key>/archive/", views.mode_archive, name="archive"),
    path("<modekey:key>/asset-requests/", views.mode_request_assets, name="request_assets"),
]
