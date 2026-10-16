"""Маршруты раздела «Каталог»."""

from __future__ import annotations

from django.urls import path

from admin_service.apps.catalog import views

app_name = "catalog"

urlpatterns = [
    path("", views.item_list, name="items"),
    path("items/<uuid:item_id>/", views.item_detail, name="item"),
    path("items/<uuid:item_id>/block/", views.item_block, name="block"),
    path("items/<uuid:item_id>/unblock/", views.item_unblock, name="unblock"),
    path("items/<uuid:item_id>/aliases/", views.item_aliases, name="aliases"),
    path("items/<uuid:item_id>/sources/", views.source_upload, name="upload"),
    path("items/<uuid:item_id>/sources/position/", views.source_position, name="position"),
    path("items/<uuid:item_id>/clue-texts/", views.clue_texts, name="clue_texts"),
    path("items/<uuid:item_id>/clue-texts/save/", views.clue_texts_save, name="clue_texts_save"),
    path(
        "items/<uuid:item_id>/clue-texts/approve/",
        views.clue_texts_approve,
        name="clue_texts_approve",
    ),
    path("republish/", views.republish, name="republish"),
    path(
        "items/<uuid:item_id>/asset-sets/<uuid:asset_set_id>/regenerate/",
        views.asset_set_regenerate,
        name="regenerate",
    ),
    path("imports/new/", views.import_new, name="import_new"),
    path("imports/<uuid:import_id>/", views.import_status, name="import"),
]
