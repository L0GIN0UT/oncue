"""Аудит-лог админки (append-only, docs/05-admin-panel.md §5).

Запись создаётся на каждое действие через admin-API — и при успехе, и при отказе сервиса.
Изменить или удалить запись нельзя ни через ORM (исключение), ни SQL-запросом (триггер
в миграции ``0001``).
"""

# ruff: noqa: ARG002 — сигнатуры переопределяемых методов Django и бэкенда аутентификации
from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, NoReturn
from uuid import UUID

from django.conf import settings
from django.db import models

from admin_service.core.models import AdminUser


class AppendOnlyError(RuntimeError):
    """Попытка изменить или удалить запись аудита."""


class AuditQuerySet(models.QuerySet["AuditEntry"]):
    """Набор записей аудита без массового изменения и удаления."""

    def update(self, **kwargs: Any) -> NoReturn:
        raise AppendOnlyError("аудит неизменяем")

    def delete(self) -> NoReturn:
        raise AppendOnlyError("аудит неизменяем")


class AuditEntry(models.Model):
    """Одно действие модератора или администратора.

    Attributes:
        actor: Кто нажал (зеркало пользователя identity).
        actor_identity_id: Идентификатор в identity — переживёт пересоздание зеркала.
        action: Машинное имя действия (``catalog.block_item``).
        service: Сервис, чей admin-API вызывался.
        target_type, target_id: Над чем действие.
        reason: Причина, введённая человеком.
        before, after: Состояние до и после (если известно).
        request_id: Сквозной id запроса (``X-Request-Id``) — связывает с логами сервиса.
        idempotency_key: Ключ идемпотентности вызова.
        outcome: ``succeeded`` или ``failed``.
        error_code: Машинный код ошибки сервиса при отказе.
    """

    class Outcome(models.TextChoices):
        SUCCEEDED = "succeeded", "успех"
        FAILED = "failed", "отказ"

    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True, db_index=True
    )
    actor: models.ForeignKey[AdminUser, AdminUser] = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_entries"
    )
    actor_identity_id: models.UUIDField[UUID | None, UUID | None] = models.UUIDField(
        null=True, blank=True
    )
    action: models.CharField[str, str] = models.CharField(max_length=64)
    service: models.CharField[str, str] = models.CharField(max_length=64)
    target_type: models.CharField[str, str] = models.CharField(max_length=64)
    target_id: models.CharField[str, str] = models.CharField(max_length=128)
    reason: models.TextField[str, str] = models.TextField()
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    request_id: models.CharField[str, str] = models.CharField(max_length=64, blank=True, default="")
    idempotency_key: models.CharField[str, str] = models.CharField(
        max_length=64, blank=True, default=""
    )
    outcome: models.CharField[str, str] = models.CharField(max_length=16, choices=Outcome.choices)
    error_code: models.CharField[str, str] = models.CharField(
        max_length=128, blank=True, default=""
    )

    objects: ClassVar[models.Manager[AuditEntry]] = AuditQuerySet.as_manager()

    class Meta:
        ordering: ClassVar = ["-created_at", "-id"]
        indexes: ClassVar = [models.Index(fields=["target_type", "target_id"])]
        verbose_name = "запись аудита"
        verbose_name_plural = "аудит"

    def __str__(self) -> str:
        return f"{self.action} {self.target_type}:{self.target_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Только вставка: повторное сохранение существующей записи запрещено."""
        if not self._state.adding:
            raise AppendOnlyError("аудит неизменяем")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Удаление запрещено."""
        raise AppendOnlyError("аудит неизменяем")
