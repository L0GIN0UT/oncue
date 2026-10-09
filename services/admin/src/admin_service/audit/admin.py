"""Просмотр аудита в django-admin: только чтение."""

# ruff: noqa: ARG002 — сигнатуры переопределяемых методов Django и бэкенда аутентификации
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.contrib import admin
from django.http import HttpRequest

from admin_service.audit.models import AuditEntry

if TYPE_CHECKING:
    _Base = admin.ModelAdmin[AuditEntry]
else:  # ModelAdmin не параметризуется в рантайме (без django_stubs_ext)
    _Base = admin.ModelAdmin


@admin.register(AuditEntry)
class AuditEntryAdmin(_Base):
    """Список и карточка записей аудита без добавления, правки и удаления."""

    list_display = ("created_at", "actor", "action", "target_type", "target_id", "outcome")
    list_filter = ("action", "outcome", "service")
    search_fields = ("target_id", "reason", "request_id", "actor__display_name")
    date_hierarchy = "created_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
