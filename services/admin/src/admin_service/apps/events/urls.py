"""Маршруты раздела «События»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.events import views

app_name = "events"

urlpatterns = [
    path("", views.event_list, name="list"),
    path("new/", views.event_new, name="new"),
    path("<eventkey:event_key>/", views.event_card, name="card"),
    path("<eventkey:event_key>/edit/", views.event_edit, name="edit"),
    path("<eventkey:event_key>/map/", views.event_map, name="map"),
    path("<eventkey:event_key>/runs/new/", views.run_new, name="run_new"),
    path("<eventkey:event_key>/runs/<uuid:run_id>/publish/", views.run_publish, name="publish"),
    path("<eventkey:event_key>/runs/<uuid:run_id>/stop/", views.run_stop, name="stop"),
    path("<eventkey:event_key>/runs/<uuid:run_id>/stats/", views.run_stats, name="stats"),
]
