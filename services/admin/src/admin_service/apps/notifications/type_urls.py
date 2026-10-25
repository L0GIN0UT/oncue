"""Маршруты раздела «Типы уведомлений»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.notifications import views

app_name = "notification_types"

urlpatterns = [
    path("", views.type_list, name="list"),
    path("set/", views.type_set, name="set"),
]
