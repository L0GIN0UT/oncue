"""Маршруты раздела «Пользователи»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.users import views

app_name = "users"

urlpatterns = [
    path("", views.user_list, name="users"),
    path("<uuid:user_id>/", views.user_detail, name="user"),
    path("<uuid:user_id>/roles/", views.user_roles, name="roles"),
]
