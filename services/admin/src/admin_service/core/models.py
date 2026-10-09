"""Модели ядра: зеркало пользователя identity и inbox событий."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar
from uuid import UUID

from django.contrib.auth.models import AbstractUser
from django.db import models


class AdminUser(AbstractUser):
    """Зеркало пользователя identity в админке.

    Создаётся при первом входе; пароля в админке нет (вход только через identity).
    Права — снимок ролей и прав identity: обновляются при каждом входе и по событию
    ``identity.UserRolesChanged`` (только более новой ``identity_version``).

    Attributes:
        identity_user_id: Идентификатор пользователя в identity (ключ зеркала).
        display_name: Отображаемое имя из identity.
        identity_roles: Роли identity (снимок).
        identity_perms: Права identity (снимок) — по ним проверяется доступ к разделам.
        identity_version: Версия агрегата из последнего применённого события.
    """

    identity_user_id: models.UUIDField[UUID | None, UUID | None] = models.UUIDField(
        unique=True, null=True, blank=True
    )
    display_name: models.CharField[str, str] = models.CharField(
        max_length=80, blank=True, default=""
    )
    identity_roles = models.JSONField(default=list, blank=True)
    identity_perms = models.JSONField(default=list, blank=True)
    identity_version: models.BigIntegerField[int, int] = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "пользователь админки"
        verbose_name_plural = "пользователи админки"

    def __str__(self) -> str:
        return self.display_name or self.username


class ProcessedEvent(models.Model):
    """Inbox: отметка об обработанном событии (дедупликация доставки «хотя бы один раз»)."""

    consumer: models.CharField[str, str] = models.CharField(max_length=255)
    event_id: models.UUIDField[UUID, UUID] = models.UUIDField()
    processed_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar = [
            models.UniqueConstraint(fields=["consumer", "event_id"], name="core_inbox_unique")
        ]
