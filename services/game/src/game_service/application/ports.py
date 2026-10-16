"""Порты application-слоя game: репозитории, Unit of Work, внешние зависимости.

Реализации — ``infrastructure``; фейков для unit-тестов use-case нет намеренно: правила
игры покрыты unit-тестами домена и ядра, а use-cases — component-тестами на реальном
Postgres (транзакции, блокировки и уникальные индексы — часть их поведения).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

from game_service.domain.asset_requests import AssetRequest
from game_service.domain.daily import DailySet
from game_service.domain.modes import ConfigVersion, ModeLine
from game_service.domain.projection import CatalogItem, ClueAssets
from game_service.domain.session import GameSession, PlayerRef
from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore
from gv_game_kernel import CandidatePort, PlayableFor, PoolQuery, SubjectQuery


# ---------------------------------------------------------------- конфиги режимов
class ModeConfigRepository(Protocol):
    """Конфиги режимов (опубликованные версии и черновики)."""

    async def get_line(self, key: str, *, for_update: bool = False) -> ModeLine:
        """Режим со всеми версиями (последняя) и черновиком; пустой, если ключа нет.

        ``for_update`` — сериализует мутации одного ключа (advisory lock транзакции).
        """
        ...

    async def save_line(self, line: ModeLine) -> None:
        """Сохранить новые версии, архивную отметку и черновик."""
        ...

    async def get_version(self, key: str, version: int) -> ConfigVersion | None:
        """Версия конфига."""
        ...

    async def list_versions(self, key: str) -> list[ConfigVersion]:
        """Все версии режима по возрастанию."""
        ...

    async def list_lines(self) -> list[ModeLine]:
        """Все режимы (с последней версией и черновиком)."""
        ...

    async def latest_published(self) -> list[ConfigVersion]:
        """Последние версии режимов в статусе ``published`` (каталог режимов)."""
        ...


# ---------------------------------------------------------------- наборы дня
class DailySetRepository(Protocol):
    """Наборы дня."""

    async def get(self, mode_key: str, day: date, *, for_update: bool = False) -> DailySet | None:
        """Набор режима на день."""
        ...

    async def add(self, daily: DailySet) -> bool:
        """Вставить новый набор; ``False`` — набор на этот день уже есть (гонка)."""
        ...

    async def save(self, daily: DailySet) -> None:
        """Сохранить статус и изменённые единицы."""
        ...

    async def list_range(self, mode_key: str, day_from: date, day_to: date) -> list[DailySet]:
        """Наборы режима за период (включительно), по дням."""
        ...

    async def items_between(self, mode_key: str, day_from: date, day_to: date) -> set[UUID]:
        """Единицы наборов режима за период (для ``exclude_recent_days``)."""
        ...

    async def due_for_lock(self, mode_key: str, today: date) -> list[DailySet]:
        """Незаблокированные наборы с днём не позже ``today`` (под блокировку строк)."""
        ...


# ---------------------------------------------------------------- проекции
@dataclass(frozen=True, slots=True)
class SubjectRow:
    """Персона с числом играбельных единиц в своей роли.

    Attributes:
        person_id: ``person_id`` каталога.
        name: Имя.
        role: Роль (``artist`` | ``director`` | ``actor``).
        items: Сколько играбельных единиц у персоны в этой роли.
    """

    person_id: UUID
    name: str
    role: str
    items: int


class ProjectionRepository(Protocol):
    """Проекции каталога и ассетов."""

    async def get_item(
        self, media_item_id: UUID, *, for_update: bool = False
    ) -> CatalogItem | None:
        """Единица каталога."""
        ...

    async def get_items(self, ids: Sequence[UUID]) -> dict[UUID, CatalogItem]:
        """Единицы по списку id (отсутствующих в ответе нет)."""
        ...

    async def upsert_item(self, item: CatalogItem) -> None:
        """Создать или заменить единицу."""
        ...

    async def set_item_status(self, media_item_id: UUID, status: str, version: int) -> None:
        """Сменить статус и версию единицы."""
        ...

    async def item_asset_version(self, media_item_id: UUID, *, lock: bool = False) -> int | None:
        """Максимальная применённая версия событий ассетов единицы."""
        ...

    async def upsert_assets(self, assets: ClueAssets) -> None:
        """Создать или заменить набор под ``(media_item_id, clue_type, params_hash)``."""
        ...

    async def fail_assets(self, asset_set_id: UUID, version: int) -> bool:
        """Отметить набор ``failed``; ``False`` — набора нет в проекции."""
        ...

    async def ready_assets(
        self, ids: Sequence[UUID], clue_type: str, params_hash: str
    ) -> dict[UUID, ClueAssets]:
        """Готовые наборы единиц под параметры загадки (только активные единицы)."""
        ...

    async def count_candidates(self, query: PoolQuery, *, also: PlayableFor | None = None) -> int:
        """Сколько единиц подходит под запрос пула (``also`` — второй набор ассетов)."""
        ...

    async def has_assets(self, clue_type: str, params_hash: str) -> bool:
        """Есть ли хоть один набор ассетов под параметры загадки (профиль media есть)."""
        ...

    async def subjects(
        self,
        query: SubjectQuery,
        *,
        text_: str | None,
        limit: int,
        also: PlayableFor | None = None,
    ) -> list[SubjectRow]:
        """Персоны для марафона и темы сетки (семантика ``FakeCandidatePort.subjects``)."""
        ...

    async def search(
        self,
        *,
        q: str | None,
        domain: str | None,
        limit: int,
    ) -> list[CatalogItem]:
        """Поиск единиц по названию (админка)."""
        ...


# ---------------------------------------------------------------- сессии
class SessionRepository(Protocol):
    """Соло-сессии."""

    async def get(self, session_id: UUID, *, for_update: bool = False) -> GameSession | None:
        """Сессия по id (``for_update`` — строка под ``FOR UPDATE``)."""
        ...

    async def add(self, session: GameSession) -> bool:
        """Вставить сессию; ``False`` — уже есть попытка этого дня (частичный unique)."""
        ...

    async def save(self, session: GameSession) -> None:
        """Сохранить состояние (под блокировкой строки той же транзакции)."""
        ...

    async def find_daily(self, mode_key: str, day: date, player: PlayerRef) -> GameSession | None:
        """Попытка игрока в дейлик дня."""
        ...

    async def stale_ids(self, now: datetime, limit: int) -> list[UUID]:
        """Активные сессии без ходов дольше их ``idle_timeout_s``."""
        ...

    async def forget_player(self, user_id: UUID) -> int:
        """Обнулить ``player_id`` пользователя (``UserDeleted``); число сессий."""
        ...


class DailyStatsRepository(Protocol):
    """Статистика раундов дейлика (только пользователи)."""

    async def record(self, mode_key: str, day: date, rounds: Sequence[tuple[int, str]]) -> None:
        """Учесть итоги раундов ``[(index, outcome)]`` (``void`` не учитывается)."""
        ...

    async def get(self, mode_key: str, day: date) -> dict[int, tuple[int, int]]:
        """``{index: (played, exact)}``."""
        ...


@dataclass(frozen=True, slots=True)
class PlayerRecord:
    """Личный рекорд режима (``features.records``, D11).

    Attributes:
        mode_key: Режим.
        best: Лучший результат (очки сессии).
        session_id: Сессия рекорда.
        achieved_at: Когда поставлен.
    """

    mode_key: str
    best: int
    session_id: UUID
    achieved_at: datetime


class RecordsRepository(Protocol):
    """Личные рекорды и гистограмма результатов режима (только пользователи)."""

    async def save_result(
        self, user_id: UUID, mode_key: str, score: int, *, session_id: UUID, at: datetime
    ) -> bool:
        """Учесть результат: гистограмма +1, рекорд — если лучше; ``True`` — новый рекорд."""
        ...

    async def get(self, user_id: UUID, mode_key: str) -> PlayerRecord | None:
        """Рекорд пользователя в режиме."""
        ...

    async def for_user(self, user_id: UUID, mode_key: str | None) -> list[PlayerRecord]:
        """Рекорды пользователя (все режимы или один), по ключу режима."""
        ...

    async def percentile(self, mode_key: str, score: int) -> float | None:
        """Доля результатов режима строго ниже ``score``, % (``None`` — результатов нет)."""
        ...


class BracketStatsRepository(Protocol):
    """Статистика выборов сетки «согласны N %» по паре (только пользователи, D10)."""

    async def add_pick(
        self, mode_key: str, period_start: date, winner_id: UUID, loser_id: UUID
    ) -> None:
        """Учесть выбор ``winner`` над ``loser``."""
        ...

    async def agree_pct(
        self, mode_key: str, period_start: date, winner_id: UUID, loser_id: UUID
    ) -> float | None:
        """Доля выборов ``winner`` в паре, % (``None`` — эту пару ещё никто не выбирал)."""
        ...


class AssetRequestRepository(Protocol):
    """Запросы ассетов (ADR 0025)."""

    async def pending(self, clue_type: str, params_hash: str) -> AssetRequest | None:
        """Ожидающий запрос под эти параметры (частичный UNIQUE — не больше одного)."""
        ...

    async def add(self, request: AssetRequest) -> bool:
        """Вставить; ``False`` — ожидающий запрос под этот хэш уже есть (гонка)."""
        ...

    async def for_mode(self, mode_key: str) -> list[AssetRequest]:
        """Запросы режима, новые первыми."""
        ...

    async def mark_sent(self, clue_type: str, params_hash: str) -> int:
        """Ожидающие запросы под эти параметры → ``sent``; число изменённых."""
        ...


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Запись аудита admin-действия game (§9 п. 8)."""

    actor_id: UUID
    action: str
    target: str
    reason: str
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)


class AdminAuditLog(Protocol):
    """Аудит admin-действий (append-only)."""

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        ...


class GameUnitOfWork(UnitOfWork, Protocol):
    """Транзакция use-case с репозиториями game."""

    modes: ModeConfigRepository
    daily_sets: DailySetRepository
    projection: ProjectionRepository
    candidates: CandidatePort
    sessions: SessionRepository
    stats: DailyStatsRepository
    idempotency: IdempotencyStore
    audit: AdminAuditLog
    records: RecordsRepository
    bracket_stats: BracketStatsRepository
    asset_requests: AssetRequestRepository

    def candidates_for(self, also: PlayableFor | None) -> CandidatePort:
        """``CandidatePort`` с дополнительным требованием второго набора ассетов."""
        ...


UnitOfWorkFactory = Callable[[], GameUnitOfWork]


# ---------------------------------------------------------------- внешние зависимости
class RateLimiter(Protocol):
    """Лимит частоты (§9 п. 4)."""

    async def hit(self, bucket: str, subject: str, *, limit: int, window_s: int) -> None:
        """Учесть запрос.

        Raises:
            GameRateLimitedError: лимит превышен (``details.retry_after``).
        """
        ...


class ModeConfigSource(Protocol):
    """Опубликованные конфиги с кэшем (TTL + поколение в Redis)."""

    async def latest(self, key: str) -> ConfigVersion | None:
        """Последняя версия режима (в том числе архивная)."""
        ...

    async def version(self, key: str, version: int) -> ConfigVersion | None:
        """Конкретная версия (неизменяема — кэшируется без срока)."""
        ...

    async def published(self) -> list[ConfigVersion]:
        """Каталог режимов: последние версии в статусе ``published``."""
        ...

    async def invalidate(self) -> None:
        """Сбросить кэш во всех процессах (после публикации и архивации)."""
        ...


class TokenProvider(Protocol):
    """Сервисный токен для вызовов media-pipeline (D13, ``gv_auth.ServiceTokenProvider``)."""

    async def token(self) -> str:
        """Действующий access-токен."""
        ...

    def invalidate(self) -> None:
        """Забыть кэшированный токен (вызываемый сервис ответил 401)."""
        ...


# Any: тело ответа ручек — JSON по контракту §2.6 (собирают views, проверяет API-схема).
type Json = dict[str, Any]
type JsonMap = Mapping[str, Any]
