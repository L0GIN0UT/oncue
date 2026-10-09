"""Аудит действий в админке (кто нажал, что, когда, с какой причиной)."""

from __future__ import annotations

from django.apps import AppConfig


class AuditConfig(AppConfig):
    """Приложение ``audit``."""

    name = "admin_service.audit"
    label = "audit"
    verbose_name = "Аудит"
