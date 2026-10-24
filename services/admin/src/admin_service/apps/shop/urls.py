"""Маршруты раздела «Магазин»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.shop import views

app_name = "shop"

urlpatterns = [
    path("", views.items, name="items"),
    path("items/new/", views.item_new, name="item_new"),
    path("items/<uuid:item_id>/", views.item_edit, name="item_edit"),
    path("items/<uuid:item_id>/media/", views.item_media, name="item_media"),
    path("packs/", views.packs, name="packs"),
    path("packs/new/", views.pack_new, name="pack_new"),
    path("packs/<uuid:pack_id>/", views.pack_edit, name="pack_edit"),
    path("promos/", views.promos, name="promos"),
    path("promos/new/", views.promo_new, name="promo_new"),
    path("promos/<uuid:promo_id>/revoke/", views.promo_revoke, name="promo_revoke"),
]
