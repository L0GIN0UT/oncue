"""Маршруты раздела «Рассылки»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.notifications import views

app_name = "broadcasts"

urlpatterns = [
    path("", views.broadcast_list, name="list"),
    path("new/", views.broadcast_new, name="new"),
    path("send/", views.broadcast_send, name="send"),
]
