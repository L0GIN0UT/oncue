"""Маршруты раздела «Рейтинг» (конвертер ``ladderkey`` — ``core.converters``)."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.rating import views

app_name = "rating"

urlpatterns = [
    path("", views.ladder_list, name="ladders"),
    path("<ladderkey:key>/", views.ladder_detail, name="ladder"),
    path("<ladderkey:key>/edit/", views.ladder_edit, name="edit"),
    path("<ladderkey:key>/publish/", views.ladder_publish, name="publish"),
    path("<ladderkey:key>/rollback/", views.ladder_rollback, name="rollback"),
    path("<ladderkey:key>/seasons/create/", views.season_create, name="season_create"),
    path(
        "<ladderkey:key>/seasons/<uuid:season_id>/close/", views.season_close, name="season_close"
    ),
    path("<ladderkey:key>/leaderboard/rebuild/", views.leaderboard_rebuild, name="rebuild"),
]
