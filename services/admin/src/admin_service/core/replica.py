"""Каркас чтения данных сервисов через read-only реплики (docs/05-admin-panel.md §2).

На этапе 1 НЕ применяется (D3 / ADR 0013): каталог читается и пишется через admin-API.
Подключение в этапе 5: unmanaged-модели (``managed = False``) с ``Meta.app_label`` сервиса,
алиас БД ``<service>_replica`` в ``DATABASES`` и ``ReplicaRouter`` в ``DATABASE_ROUTERS``.
"""

# ruff: noqa: ARG002 — сигнатуры переопределяемых методов Django и бэкенда аутентификации
from __future__ import annotations

from typing import TYPE_CHECKING, Any, NoReturn

from django.contrib import admin
from django.db import models
from django.http import HttpRequest


class ReplicaRouter:
    """Направляет чтение моделей сервиса в его реплику, запись и миграции — запрещает.

    Args:
        service_apps: ``app_label`` → алиас БД реплики.
    """

    def __init__(self, service_apps: dict[str, str] | None = None) -> None:
        self.service_apps = service_apps or {}

    def db_for_read(self, model: type[models.Model], **hints: Any) -> str | None:
        """Реплика сервиса для его моделей."""
        return self.service_apps.get(model._meta.app_label)

    def db_for_write(self, model: type[models.Model], **hints: Any) -> str | None:
        """Запись в чужую БД запрещена: только через admin-API сервиса."""
        if model._meta.app_label in self.service_apps:
            raise PermissionError("данные сервиса меняются только через его admin-API")
        return None

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool | None:
        """Схемой реплик владеет сервис, а не админка."""
        if app_label in self.service_apps or db in self.service_apps.values():
            return False
        return None


if TYPE_CHECKING:
    _Base = admin.ModelAdmin[Any]
else:  # ModelAdmin не параметризуется в рантайме (без django_stubs_ext)
    _Base = admin.ModelAdmin


class ServiceModelAdmin(_Base):
    """Базовый ModelAdmin для моделей сервиса поверх реплики: только чтение.

    Изменения — кастомными действиями через ``ServiceClient``; стандартные ``save_model`` и
    ``delete_model`` запрещены, чтобы обойти инварианты и outbox сервиса было невозможно.
    """

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> NoReturn:  # noqa: FBT001 — сигнатура Django
        raise PermissionError("используйте действие, вызывающее admin-API сервиса")

    def delete_model(self, request: HttpRequest, obj: Any) -> NoReturn:
        raise PermissionError("используйте действие, вызывающее admin-API сервиса")
