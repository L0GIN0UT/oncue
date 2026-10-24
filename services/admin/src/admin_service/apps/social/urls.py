"""Маршруты раздела «Модерация»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.social import views

app_name = "social"

urlpatterns = [
    path("", views.cases, name="cases"),
    path("cases/<uuid:case_id>/", views.case_detail, name="case"),
    path("cases/<uuid:case_id>/claim/", views.case_claim, name="claim"),
    path("cases/<uuid:case_id>/escalate/", views.case_escalate, name="escalate"),
    path("cases/<uuid:case_id>/resolve/", views.case_resolve, name="resolve"),
    path("players/", views.player_search, name="players"),
    path("players/<uuid:user_id>/", views.player, name="player"),
    path("players/<uuid:user_id>/sanctions/", views.sanction_grant, name="grant"),
    path("players/<uuid:user_id>/sanctions/revoke/", views.sanction_revoke, name="revoke"),
    path("config/", views.configs, name="configs"),
    path("config/edit/", views.config_edit, name="edit"),
    path("config/publish/", views.config_publish, name="publish"),
    path("config/rollback/", views.config_rollback, name="rollback"),
]
