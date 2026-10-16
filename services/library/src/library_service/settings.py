"""Настройки library (переменные окружения ``GV_*``, план этапа 3b §2.8)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gv_service import ServiceSettings, UgcSettings


class LibraryLimits(BaseModel):
    """Лимиты медиатеки (D11–D12, ADR 0031): ``GV_LIBRARY__LIMITS__*``.

    Attributes:
        custom_shelves_max: Своих полок у пользователя.
        items_per_shelf_max: Единиц на одной полке (системной или своей).
        shelf_title_max: Длина названия своей полки после нормализации (кодовые точки).
        saves_per_min: Сохранений и удалений в минуту на пользователя (fail-open).
    """

    model_config = ConfigDict(frozen=True)

    custom_shelves_max: int = Field(default=50, ge=0, le=1000)
    items_per_shelf_max: int = Field(default=2000, ge=1, le=100000)
    shelf_title_max: int = Field(default=60, ge=1, le=200)
    saves_per_min: int = Field(default=60, ge=1)


class LibrarySettings(BaseModel):
    """Настройки медиатеки: ``GV_LIBRARY__*``.

    Attributes:
        shelves_config: YAML системных полок (ключ, домены, ``title_key``,
            ``exclusive_with``); пусто или ``None`` — встроенный ``config/shelves.yaml``
            пакета сервиса (``GV_LIBRARY__SHELVES_CONFIG``).
        limits: Лимиты.
        purge_batch: Строк ``shelf_items`` за одну порцию удаления пользователя
            (``identity.UserDeleted``), каждая порция — своя транзакция.
        purge_users_per_run: Пользователей за один запуск задачи удаления.
        purge_interval_s: Пауза между запусками задачи ``library.users.purge`` (relay).
        purge_jitter_s: Случайная добавка к паузе.
    """

    model_config = ConfigDict(frozen=True)

    shelves_config: Path | None = None
    limits: LibraryLimits = LibraryLimits()
    purge_batch: int = Field(default=500, ge=1, le=10000)
    purge_users_per_run: int = Field(default=20, ge=1, le=1000)
    purge_interval_s: float = Field(default=60, gt=0)
    purge_jitter_s: float = Field(default=10, ge=0)

    @field_validator("shelves_config", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # compose передаёт `${GV_LIBRARY__SHELVES_CONFIG:-}`: пустая строка = «не задан».
        return None if value == "" else value


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "library"
    library: LibrarySettings = LibrarySettings()
    ugc: UgcSettings = UgcSettings()
