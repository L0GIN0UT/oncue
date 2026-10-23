"""Маршруты раздела «Прогрессия»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.progression import views

app_name = "progression"

urlpatterns = [
    path("", views.configs, name="configs"),
    path("edit/", views.config_edit, name="edit"),
    path("publish/", views.config_publish, name="publish"),
    path("rollback/", views.config_rollback, name="rollback"),
    path("wheel/", views.wheel_preview, name="wheel"),
    path("quests/", views.quests_badges, name="quests"),
    path("ledger/", views.ledger_search, name="ledger"),
    path("ledger/<uuid:user_id>/", views.ledger_user, name="ledger_user"),
    path("ledger/<uuid:user_id>/grant/", views.ledger_grant, name="grant"),
    path("ledger/<uuid:user_id>/reverse/", views.ledger_reverse, name="reverse"),
]
