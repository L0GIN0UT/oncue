"""Порты ядра: доступ плагинов к внешним данным.

Реализации — в сервисах (game: ``MediaPipelineAssetClient``, SQL-``CandidatePort``),
фейки — в ``gv_testkit.game``. Ядро вызывает порты, но не знает про HTTP и БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from gv_game_kernel.refs import AssetRef, MediaItemRef


@dataclass(frozen=True, slots=True)
class ClueAsset:
    """Выданная короткоживущая ссылка на ассет уровня (ответ media-pipeline).

    Attributes:
        level: Уровень (с 1).
        url: Подписанная ссылка ``/media/a/<hex>.<ext>``.
        expires_at: Момент истечения (UTC).
        mime: MIME-тип ассета.
    """

    level: int
    url: str
    expires_at: datetime
    mime: str


class AssetPort(Protocol):
    """Выдача ссылок на ассеты загадки."""

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        """Ссылка на ассет уровня ``level`` для субъекта ``subject`` (``sess:<id>``).

        Raises:
            gv_core.errors.GoneError: набор ассетов выключен (раунд становится ``void``).
            gv_core.errors.DependencyUnavailableError: media-pipeline недоступен.
            gv_core.errors.RateLimitedError: превышен лимит выдачи.
        """
        ...


@dataclass(frozen=True, slots=True)
class PlayableFor:
    """Требование играбельности: есть готовые ассеты под эти параметры загадки (D7).

    Attributes:
        clue_type: Ключ ClueType.
        params_hash: ``params_hash(clue.params)``.
    """

    clue_type: str
    params_hash: str


@dataclass(frozen=True, slots=True)
class PoolQuery:
    """Запрос кандидатов из пула (собирается Source из ``pool`` конфига и фильтров).

    Attributes:
        domain: Домен режима.
        kinds: Допустимые виды единиц (пусто — любые).
        genres_any: Хотя бы один жанр из списка (пусто — без фильтра).
        year_from: Год не раньше.
        year_to: Год не позже.
        min_popularity: Популярность не ниже.
        playable_for: Только единицы с готовыми ассетами под параметры загадки.
        exclude_ids: Исключить единицы (например, из недавних наборов дня).
    """

    domain: str
    kinds: frozenset[str] = frozenset()
    genres_any: frozenset[str] = frozenset()
    year_from: int | None = None
    year_to: int | None = None
    min_popularity: float | None = None
    playable_for: PlayableFor | None = None
    exclude_ids: frozenset[UUID] = frozenset()


class CandidatePort(Protocol):
    """Кандидаты для розыгрыша раундов."""

    async def candidates(self, query: PoolQuery, limit: int) -> list[MediaItemRef]:
        """До ``limit`` единиц, подходящих под запрос, в порядке ``id`` (детерминизм draw)."""
        ...


class CollectionPort(Protocol):
    """Единицы сборника (адаптер в game — этап 3, проекции сборников)."""

    async def items(self, collection_id: UUID, limit: int) -> list[MediaItemRef]:
        """До ``limit`` единиц сборника в порядке сборника."""
        ...
