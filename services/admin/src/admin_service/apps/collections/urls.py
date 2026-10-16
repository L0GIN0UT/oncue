"""Маршруты раздела «Сборники»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.collections import views

app_name = "collections"

urlpatterns = [
    path("", views.collection_list, name="list"),
    path("new/", views.curated_new, name="new"),
    path("<uuid:collection_id>/", views.collection_detail, name="detail"),
    path("<uuid:collection_id>/edit/", views.curated_edit, name="edit"),
    path("<uuid:collection_id>/hide/", views.collection_hide, name="hide"),
    path("<uuid:collection_id>/unhide/", views.collection_unhide, name="unhide"),
    path("<uuid:collection_id>/featured/", views.collection_featured, name="featured"),
    path(
        "<uuid:collection_id>/author-restriction/",
        views.author_restriction,
        name="author_restriction",
    ),
]
