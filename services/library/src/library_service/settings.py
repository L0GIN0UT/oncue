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
    """

    model_config = ConfigDict(frozen=True)

    shelves_config: Path | None = None
    limits: LibraryLimits = LibraryLimits()

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
