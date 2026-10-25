"""Оркестратор ``gv dev seed`` — наполнение запущенного стека для e2e (дейлики, режимы 3a,
кураторские сборники 3b, онлайн-шаблоны 4a).

Только для local/ci: сидер работает на хосте и обращается к admin/internal-ручкам сервисов
напрямую (в dev/ci они опубликованы на 127.0.0.1 через ``compose.services.dev.yml``; на
gateway этих ручек нет). Шаги идемпотентны и безопасны при повторном запуске: все мутации
несут стабильный ``Idempotency-Key`` и ``reason``, а ожидание готовности — опрос с таймаутом.

Последовательность (см. ``seeds/catalog/README.md`` и ``seeds/media/README.md``):

1. Аутентификация: сервисный токен (client credentials, ``audience=identity``) → вход в
   админку (``/internal/v1/admin-sessions``) → полученный admin-токен идёт ``Bearer`` во все
   ``/admin/v1/*``.
2. Импорт каталога из файлов (``IMPORT_FILES``: сиды этапа 2 и синтетика этапа 3a) с
   ожиданием завершения задания и появления единиц в списке.
3. Генерация синтетических исходников и загрузка всех видов на единицу (``SOURCES_BY_DOMAIN``:
   ``audio`` для музыки; шесть ``frame`` с ``position``, ``soundtrack`` и ``audio_clip`` для
   фильмов) с ``origin=seed``; ожидание готовности всех наборов единицы и статических
   профилей media-pipeline.
4. Ожидание играбельного пула каждого режима из ``seeds/game/modes`` в проекции game
   (у онлайн-шаблонов этапа 4 — не меньше ``session.rounds``) и публикации онлайн-шаблонов
   (``GET /v1/modes?session=online``).
5. Перегенерация наборов **всех** плановых источников (дейлики — день, сетка — неделя) на
   текущий период с проверкой пригодности набора.
6. Кураторские сборники ``seeds/collections/*.yaml`` (план этапа 3b §0 п. 1, D14): создание
   (``POST /admin/v1/collections``) или новая версия (``PUT``) через admin-API collections —
   с ожиданием единиц в проекции collections (``collections.item_unavailable``), набора своих
   эмодзи со ``scope`` сборника в media-pipeline и проекции сборника в game
   (``GET /v1/collections/{id}/modes`` → режим по умолчанию ``available``).
7. Онлайн-лобби по сборнику (план этапа 4 §0 п. 2): у каждого шаблона ``*.lobby.*`` с
   источником ``collection`` есть кураторский сборник домена с ≥ ``session.rounds``
   единиц, и они играбельны под сам шаблон (``playable_for_mode``).

Пользователей засев не создаёт: e2e регистрируют свежих игроков в каждом прогоне (ссылка
подтверждения — из лога identity), иначе брошенная прошлым прогоном комната держала бы
игрока (409 ``match.active_room_conflict``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml

# Причина действия для аудита admin-мутаций (``gv_service.Reason``: 3..500 символов).
REASON = "сиды e2e"
# Audience сервисного токена для входа в админку (identity выдаёт токен с scope admin_sessions).
IDENTITY_AUDIENCE = "identity"
# Файлы импорта каталога (источник ``file``); импортируются те, что лежат в каталоге сидов.
# ``file_id`` catalog — только имя файла без подкаталогов, поэтому синтетика лежит рядом.
IMPORT_FILES = (
    "films.jsonl",
    "tracks.jsonl",
    "synthetic-films.jsonl",
    "synthetic-tracks.jsonl",
)
# Часовой пояс плановых наборов по умолчанию, если в сиде режима не задан ``timezone``.
DEFAULT_TIMEZONE = "Europe/Moscow"
# Происхождение синтетических исходников (D6 плана этапа 3a) и пометка лицензии (ADR 0014).
SOURCE_ORIGIN = "seed"
LICENSE_NOTE = "синтетика seeds/media/generate.py, прав третьих лиц нет (ADR 0014)"
# Подстрока названий синтетических единиц (``seeds/catalog/synthetic-*.jsonl``): проекция
# game ищет по названию и отдаёт не больше 50 строк, поэтому синтетику спрашиваем отдельно.
SYNTHETIC_TITLE_QUERY = "Синтетика"
# Лимит строк поиска проекции game (``/admin/v1/projection/items``, ``limit ≤ 50``).
PROJECTION_LIMIT = 50
# Кадров ``frame_sequence`` на фильм (профиль ``frame_sequence/default`` и генератор).
FRAMES_PER_FILM = 6
# Код ошибки публикации collections: единицы ещё нет в проекции каталога collections
# (``MediaItemCreated`` не обработан) — засев ждёт, а не падает.
ITEM_UNAVAILABLE = "collections.item_unavailable"
# Вид владельца набора ассетов своих загадок сборника (``AssetsReady.scope.kind``, ADR 0029).
COLLECTION_SCOPE = "collection"
# Лимит строк списка сборников admin-API collections (``limit ≤ 100``).
ADMIN_COLLECTIONS_LIMIT = 100
# Вид сессии соло-режима (``session.kind``); остальные виды — онлайн (match, ADR 0032).
SOLO_KIND = "solo"
# Тип источника раундов «по сборнику» (``source.type``): онлайн-лобби по сборнику (D12).
COLLECTION_SOURCE = "collection"


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Исходник единицы: вид media-pipeline, шаблон имени файла генератора и позиция.

    Attributes:
        kind: ``SourceKind`` загрузки (``audio``, ``frame``, ``soundtrack``, ``audio_clip``).
        filename: Шаблон имени в каталоге генератора: ``{slug}`` — ключ с ``-`` вместо ``:``
            (``film-x``), ``{short}`` — ключ без вида (``x``).
        mime: MIME файла.
        position: Порядок среди исходников вида (кадры ``frame_sequence``: 1 — самый сложный).
    """

    kind: str
    filename: str
    mime: str
    position: int = 1

    def path(self, out: Path, seed_key: str) -> Path:
        """Файл исходника единицы в каталоге генератора."""
        slug = seed_key.replace(":", "-")
        short = seed_key.split(":", 1)[-1].replace(":", "-")
        return out / self.filename.format(slug=slug, short=short)


# Что загружать по домену единицы (имена — ``seeds/media/generate.py``). Кадры идут по
# возрастанию позиции: «лучший» кадр ``frame_progressive`` — самый свежий исходник вида.
SOURCES_BY_DOMAIN: Mapping[str, tuple[SourceSpec, ...]] = {
    "music": (SourceSpec("audio", "{slug}.m4a", "audio/mp4"),),
    "film": (
        *(
            SourceSpec("frame", f"{{slug}}-{n}.jpg", "image/jpeg", position=n)
            for n in range(1, FRAMES_PER_FILM + 1)
        ),
        SourceSpec("soundtrack", "soundtrack-{short}.m4a", "audio/mp4"),
        SourceSpec("audio_clip", "clip-{short}.m4a", "audio/mp4"),
    ),
}

# Статусы запуска события, при которых определение неизменяемо (409 ``event_definition_locked``).
LOCKING_RUN_STATUSES = frozenset({"published", "active"})

ClientFactory = Callable[[str], httpx.Client]
EchoFn = Callable[[str], None]
MediaGenerator = Callable[[Path], None]


class SeedError(RuntimeError):
    """Сидирование не удалось (неожиданный ответ сервиса, таймаут ожидания и т.п.)."""


@dataclass(frozen=True, slots=True)
class SeedConfig:
    """Параметры запуска сидера.

    Attributes:
        identity_url: Базовый адрес identity (internal-ручки).
        catalog_url: Базовый адрес catalog (admin-ручки).
        media_url: Базовый адрес media-pipeline (admin-ручки).
        game_url: Базовый адрес game (admin-ручки и публичные режимы сборника).
        collections_url: Базовый адрес collections (admin-ручки кураторских сборников).
        progression_url: Базовый адрес progression (admin-ручки предметов, паков и конфига).
        progression_dir: Сиды progression (по умолчанию ``seeds/progression``); нет
            ``items/items.yaml`` - шаг пропускается.
        social_url: Базовый адрес social (admin-ручки конфига, ADR 0053).
        admin_login: Логин администратора (e-mail или никнейм).
        admin_password: Пароль администратора.
        service_client_id: Идентификатор сервисного клиента (по умолчанию ``admin``).
        service_secret: Сырой секрет сервисного клиента.
        repo_root: Корень монорепозитория (для запуска генератора и поиска сидов).
        day: День плановых наборов; ``None`` — сегодня в часовом поясе режима. Для недельных
            наборов берётся начало недели этого дня.
        catalog_dir: Каталог файлов импорта (по умолчанию ``seeds/catalog``).
        media_out_dir: Куда генератор кладёт исходники (по умолчанию ``seeds/media/out``).
        modes_dir: Сиды режимов game (по умолчанию ``seeds/game/modes``).
        collections_dir: Кураторские сборники (по умолчанию ``seeds/collections``).
        http_timeout_s: Таймаут одного HTTP-запроса.
        poll_interval_s: Пауза между опросами готовности.
        import_timeout_s: Предел ожидания завершения импорта и появления единиц.
        assets_timeout_s: Предел ожидания готовности наборов ассетов, пулов, наборов периода и
            проекций сборников.
    """

    identity_url: str
    catalog_url: str
    media_url: str
    game_url: str
    collections_url: str
    admin_login: str
    admin_password: str
    service_secret: str
    repo_root: Path
    service_client_id: str = "admin"
    day: date | None = None
    catalog_dir: Path | None = None
    media_out_dir: Path | None = None
    modes_dir: Path | None = None
    collections_dir: Path | None = None
    progression_url: str = "http://127.0.0.1:8109"
    progression_dir: Path | None = None
    social_url: str = "http://127.0.0.1:8111"
    http_timeout_s: float = 30.0
    poll_interval_s: float = 1.0
    import_timeout_s: float = 60.0
    assets_timeout_s: float = 300.0

    def catalog_path(self) -> Path:
        """Каталог файлов импорта."""
        return self.catalog_dir or self.repo_root / "seeds" / "catalog"

    def media_out_path(self) -> Path:
        """Каталог синтетических исходников."""
        return self.media_out_dir or self.repo_root / "seeds" / "media" / "out"

    def modes_path(self) -> Path:
        """Каталог сидов режимов."""
        return self.modes_dir or self.repo_root / "seeds" / "game" / "modes"

    def collections_path(self) -> Path:
        """Каталог кураторских сборников."""
        return self.collections_dir or self.repo_root / "seeds" / "collections"

    def progression_path(self) -> Path:
        """Каталог сидов progression (конфиг, предметы, паки, картинки)."""
        return self.progression_dir or self.repo_root / "seeds" / "progression"


@dataclass(slots=True)
class _ItemRef:
    """Единица каталога, сопоставленная сидером."""

    media_item_id: str
    domain: str


@dataclass(frozen=True, slots=True)
class ModePlan:
    """Что сидер ждёт от режима (из его сида ``seeds/game/modes/<key>.yaml``).

    Attributes:
        key: Ключ режима.
        min_playable: Сколько играбельных единиц нужно в проекции game: ``pool.min_playable``,
            иначе минимум темы/персоны (``subject.min_items``/``min_items``), иначе ``size``;
            у онлайн-шаблона — не меньше ``session.rounds`` (план матча разыгрывает столько
            раундов сразу, иначе ``game.pool_too_small`` на старте матча).
        period: ``day``/``week`` — у режима плановые наборы (общие поля периода
            ``schedule_ahead_*``); ``None`` — наборов периода нет.
        timezone: Часовой пояс периода.
        size: Ожидаемое число единиц набора периода (``size``), если задано.
        themed: У набора периода есть тема-персона (сетка: ``subject``).
        domain: Домен режима (``film``/``music``), если задан.
        online: Онлайн-шаблон (``session.kind`` — не ``solo``; играет match, ADR 0032).
        uses_collection: Источник раундов — сборники (``source.type: collection``).
        rounds: Раунды онлайн-сессии по умолчанию (``session.rounds``).
    """

    key: str
    min_playable: int
    period: str | None
    timezone: str
    size: int | None
    themed: bool
    domain: str | None = None
    online: bool = False
    uses_collection: bool = False
    rounds: int | None = None

    @property
    def lobby_by_collection(self) -> bool:
        """Онлайн-шаблон по сборнику: e2e нужен кураторский сборник с ≥ ``min_playable``."""
        return self.online and self.uses_collection

    def period_start(self, day: date) -> date:
        """Начало периода дня ``day`` (неделя — с понедельника, как ``period_start`` ядра)."""
        if self.period == "week":
            return day - timedelta(days=day.weekday())
        return day


def mode_plan(spec: Mapping[str, Any]) -> ModePlan:
    """Требования сидера к режиму по телу сида (без знания типов источников)."""
    params = spec.get("source", {}).get("params", {}) or {}
    pool = params.get("pool", {}) or {}
    subject = params.get("subject", {}) or {}
    raw_min = pool.get("min_playable", subject.get("min_items", params.get("min_items")))
    size = params.get("size")
    min_playable = int(raw_min if raw_min is not None else size or 1)
    scheduled = any(name.startswith("schedule_ahead_") for name in params)
    session = spec.get("session", {}) or {}
    online = str(session.get("kind", SOLO_KIND)) != SOLO_KIND
    raw_rounds = session.get("rounds") if online else None
    rounds = int(raw_rounds) if raw_rounds is not None else None
    if rounds is not None:
        min_playable = max(min_playable, rounds)
    domain = spec.get("domain")
    return ModePlan(
        key=str(spec["key"]),
        min_playable=min_playable,
        period=str(params.get("period", "day")) if scheduled else None,
        timezone=str(params.get("timezone", DEFAULT_TIMEZONE)),
        size=int(size) if size is not None else None,
        themed=bool(subject),
        domain=str(domain) if domain is not None else None,
        online=online,
        uses_collection=spec.get("source", {}).get("type") == COLLECTION_SOURCE,
        rounds=rounds,
    )


@dataclass(frozen=True, slots=True)
class CollectionItemSpec:
    """Единица кураторского сборника: ключ сида каталога и свои эмодзи куратора."""

    seed_key: str
    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class CollectionSpec:
    """Кураторский сборник из ``seeds/collections/<key>.yaml`` (формат — README каталога).

    Attributes:
        key: Ключ засева: часть ``Idempotency-Key`` мутаций (на сервере не хранится).
        title: Название — по нему засев находит уже созданный кураторский сборник.
        domain: ``film``/``music``.
        default_mode_key: Режим сборника по умолчанию (``<domain>.collection.*``).
        items: Единицы в порядке сборника.
        description: Описание.
        tags: Ключи тегов из ``meta.yaml`` collections.
        cover: Пресет обложки из ``meta.yaml`` collections.
        visibility: ``public``/``unlisted``.
        featured_rank: Отметка «рекомендуемое» с этим порядком (``None`` — не ставить).
    """

    key: str
    title: str
    domain: str
    default_mode_key: str
    items: tuple[CollectionItemSpec, ...]
    description: str | None = None
    tags: tuple[str, ...] = ()
    cover: str | None = None
    visibility: str = "public"
    featured_rank: int | None = None

    def body(self, refs: Mapping[str, _ItemRef]) -> dict[str, Any]:
        """Тело ``CuratedCollectionIn``/``Update`` без ``domain``, ``reason``, ``base_version``."""
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "cover": {"preset": self.cover} if self.cover else None,
            "default_mode_key": self.default_mode_key,
            "visibility": self.visibility,
            "items": [
                {
                    "media_item_id": refs[item.seed_key].media_item_id,
                    "custom": {"emoji": item.emoji} if item.emoji else None,
                }
                for item in self.items
            ],
        }

    def emoji_items(self) -> list[CollectionItemSpec]:
        """Единицы со своими эмодзи (для них media-pipeline строит набор со ``scope``)."""
        return [item for item in self.items if item.emoji]


def lobby_collections(
    plans: Iterable[ModePlan], specs: Iterable[CollectionSpec]
) -> list[tuple[ModePlan, CollectionSpec]]:
    """Кураторский сборник засева для каждого онлайн-шаблона по сборнику (лобби, D12).

    Матч по сборнику разыгрывает ``session.rounds`` раундов сразу, поэтому нужен сборник
    домена шаблона с ≥ ``min_playable`` единиц (берётся самый большой). Сидер проверяет это
    до долгих шагов: без такого сборника e2e лобби упал бы на старте матча с
    ``game.pool_too_small``.

    Raises:
        SeedError: подходящего сборника нет.
    """
    pool = list(specs)
    result: list[tuple[ModePlan, CollectionSpec]] = []
    for plan in plans:
        if not plan.lobby_by_collection:
            continue
        fitting = [
            s
            for s in pool
            if (plan.domain is None or s.domain == plan.domain)
            and len(s.items) >= plan.min_playable
        ]
        if not fitting:
            raise SeedError(
                f"для онлайн-шаблона {plan.key} нет кураторского сборника домена "
                f"{plan.domain} с ≥ {plan.min_playable} единиц в seeds/collections"
            )
        result.append((plan, max(fitting, key=lambda s: len(s.items))))
    return result


def load_collection_spec(path: Path) -> CollectionSpec:
    """Прочитать сид сборника; ошибка формата — :class:`SeedError` с именем файла."""
    try:
        raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
        items = tuple(_collection_item(row) for row in raw["items"])
        rank = raw.get("featured_rank")
        return CollectionSpec(
            key=str(raw["key"]),
            title=str(raw["title"]),
            domain=str(raw["domain"]),
            default_mode_key=str(raw["default_mode_key"]),
            items=items,
            description=str(raw["description"]) if raw.get("description") else None,
            tags=tuple(str(t) for t in raw.get("tags") or ()),
            cover=str(raw["cover"]) if raw.get("cover") else None,
            visibility=str(raw.get("visibility", "public")),
            featured_rank=int(rank) if rank is not None else None,
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise SeedError(f"сид сборника {path.name} некорректен: {exc!r}") from exc


def _mapping(raw: object) -> dict[str, Any]:
    """Корень сида — словарь (иначе ``TypeError``, его оборачивает загрузчик)."""
    if not isinstance(raw, dict):
        raise TypeError("ожидается словарь")
    return raw


def _collection_item(row: object) -> CollectionItemSpec:
    """Строка ``items``: ключ сида или ``{item, emoji}``."""
    if isinstance(row, str):
        return CollectionItemSpec(seed_key=row)
    if isinstance(row, dict):
        emoji = row.get("emoji")
        return CollectionItemSpec(seed_key=str(row["item"]), emoji=str(emoji) if emoji else None)
    raise TypeError(f"единица сборника: {row!r}")


def _content_digest(body: Mapping[str, Any]) -> str:
    """Короткий отпечаток содержимого: новый ключ идемпотентности на каждую правку сида."""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _published_body(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Тело опубликованной версии (``AdminCollectionDetail``) в форме ``CollectionSpec.body``."""
    items = sorted(detail.get("items") or [], key=lambda i: int(i["position"]))
    cover = detail.get("cover")
    return {
        "title": detail.get("title"),
        "description": detail.get("description"),
        "tags": list(detail.get("tags") or []),
        "cover": {"preset": cover["preset"]} if cover else None,
        "default_mode_key": detail.get("default_mode_key"),
        "visibility": detail.get("visibility"),
        "items": [
            {
                "media_item_id": str(i["media_item_id"]),
                "custom": {"emoji": i["custom"]["emoji"]}
                if i.get("custom") and i["custom"].get("emoji")
                else None,
            }
            for i in items
        ],
    }


def default_client_factory(timeout_s: float) -> ClientFactory:
    """Фабрика HTTP-клиентов с общим таймаутом (один клиент на сервис)."""

    def factory(base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, timeout=timeout_s)

    return factory


def default_media_generator(repo_root: Path) -> MediaGenerator:
    """Генератор исходников через ``uv run python seeds/media/generate.py``."""

    def generate(out: Path) -> None:
        subprocess.run(
            ["uv", "run", "python", "seeds/media/generate.py", "--out", str(out)],
            cwd=repo_root,
            check=True,
        )

    return generate


class Seeder:
    """Пошаговое наполнение стека. Шаги идемпотентны; порядок задаёт :meth:`run`."""

    def __init__(
        self,
        config: SeedConfig,
        *,
        client_factory: ClientFactory | None = None,
        media_generator: MediaGenerator | None = None,
        echo: EchoFn | None = None,
    ) -> None:
        self._cfg = config
        factory = client_factory or default_client_factory(config.http_timeout_s)
        self._generate_media = media_generator or default_media_generator(config.repo_root)
        self._echo = echo or print
        self._identity = factory(config.identity_url)
        self._catalog = factory(config.catalog_url)
        self._media = factory(config.media_url)
        self._game = factory(config.game_url)
        # Публичные ручки game (режимы сборника) — без admin-токена: тот выпущен для admin-ручек,
        # а ``/v1/collections/{id}/modes`` открыт и анониму.
        self._game_public = factory(config.game_url)
        self._collections = factory(config.collections_url)
        self._progression = factory(config.progression_url)
        self._social = factory(config.social_url)
        self._admin_token: str | None = None

    def _clients(self) -> tuple[httpx.Client, ...]:
        return (
            self._identity,
            self._catalog,
            self._media,
            self._game,
            self._game_public,
            self._collections,
            self._progression,
            self._social,
        )

    def close(self) -> None:
        """Закрыть все HTTP-клиенты."""
        for client in self._clients():
            client.close()

    def __enter__(self) -> Seeder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ оркестрация
    def run(self) -> None:
        """Выполнить все шаги сидирования."""
        self.authenticate()
        expected = self._expected_keys()
        plans = self.mode_plans()
        # Сиды сборников проверяются до долгих шагов: опечатка в ключе единицы видна сразу.
        collections = self.collection_specs(expected)
        lobby_collections = self.online_collection_specs(plans, collections)
        self.import_catalog()
        items = self.resolve_items(expected)
        self.generate_sources()
        self.upload_sources(items)
        self.wait_profiles()
        self.wait_online_templates(plans)
        self.wait_playable_pools(plans)
        self.regenerate_period_sets(plans)
        self.seed_collections(collections, items)
        self.wait_online_collections(lobby_collections, items)
        self.seed_progression()
        self.seed_events()
        self.seed_social()
        self._echo("seed: готово")

    # ------------------------------------------------------------------ шаг 1: вход
    def authenticate(self) -> str:
        """Получить сервисный токен и войти в админку; вернуть admin-токен."""
        self._echo("seed: аутентификация")
        service_token = self._service_token()
        resp = self._identity.post(
            "/internal/v1/admin-sessions",
            headers={"Authorization": f"Bearer {service_token}"},
            json={"login": self._cfg.admin_login, "password": self._cfg.admin_password},
        )
        self._check(resp, {200}, "вход в админку")
        token = str(resp.json()["access_token"])
        self._admin_token = token
        # admin-токен — Bearer для всех admin-ручек каталога, медиа, игры и сборников.
        for client in (
            self._catalog,
            self._media,
            self._game,
            self._collections,
            self._progression,
            self._social,
        ):
            client.headers["Authorization"] = f"Bearer {token}"
        return token

    def _service_token(self) -> str:
        resp = self._identity.post(
            "/internal/v1/service-tokens",
            json={
                "client_id": self._cfg.service_client_id,
                "client_secret": self._cfg.service_secret,
                "audience": IDENTITY_AUDIENCE,
            },
        )
        self._check(resp, {200}, "сервисный токен")
        return str(resp.json()["access_token"])

    # ------------------------------------------------------------------ шаг 2: импорт
    def import_catalog(self) -> None:
        """Импортировать файлы каталога и дождаться завершения заданий."""
        for file_id in self._present_import_files():
            stem = file_id.removesuffix(".jsonl")
            self._echo(f"seed: импорт {file_id}")
            resp = self._catalog.post(
                "/admin/v1/imports",
                headers={"Idempotency-Key": f"seed-import-{stem}"},
                json={"source": "file", "file_id": file_id, "reason": REASON},
            )
            self._check(resp, {200, 202}, f"импорт {file_id}")
            import_id = str(resp.json()["import_id"])
            self._wait_import(import_id, file_id)

    def _wait_import(self, import_id: str, file_id: str) -> None:
        def check() -> bool:
            resp = self._catalog.get(f"/admin/v1/imports/{import_id}")
            self._check(resp, {200}, f"состояние импорта {file_id}")
            view = resp.json()
            status = view["status"]
            if status == "failed":
                raise SeedError(f"импорт {file_id} завершился ошибкой: {view.get('error')}")
            return bool(status == "done")

        self._poll(
            check,
            timeout_s=self._cfg.import_timeout_s,
            what=f"завершение импорта {file_id}",
        )

    # ------------------------------------------------------------------ шаг 2b: единицы
    def resolve_items(self, expected_keys: Iterable[str]) -> dict[str, _ItemRef]:
        """Дождаться появления единиц и сопоставить ключ сида → ``media_item_id``/домен."""
        expected = set(expected_keys)
        self._echo(f"seed: ожидание {len(expected)} единиц в каталоге")
        seen: dict[str, _ItemRef] = {}

        def check() -> bool:
            self._collect_items(seen)
            return expected <= set(seen)

        self._poll(
            check,
            timeout_s=self._cfg.import_timeout_s,
            what="появление единиц каталога",
            on_timeout=lambda: f"не найдены единицы: {sorted(expected - set(seen))}",
        )
        return {key: seen[key] for key in expected}

    def _collect_items(self, seen: dict[str, _ItemRef]) -> None:
        """Пройти список единиц и добрать карточки новых (seed — только в детали единицы)."""
        cursor: str | None = None
        known_ids = {ref.media_item_id for ref in seen.values()}
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = self._catalog.get("/admin/v1/items", params=params)
            self._check(resp, {200}, "список единиц")
            page = resp.json()
            for summary in page["items"]:
                item_id = str(summary["id"])
                if item_id in known_ids:
                    continue
                ref = self._item_seed(item_id)
                if ref is not None:
                    seen[ref[0]] = _ItemRef(media_item_id=item_id, domain=ref[1])
                    known_ids.add(item_id)
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def _item_seed(self, item_id: str) -> tuple[str, str] | None:
        """Ключ сида и домен единицы из её карточки (``external_refs.seed``)."""
        resp = self._catalog.get(f"/admin/v1/items/{item_id}")
        self._check(resp, {200}, f"карточка единицы {item_id}")
        detail = resp.json()
        seed = detail.get("external_refs", {}).get("seed")
        if seed is None:
            return None
        return str(seed), str(detail["domain"])

    # ------------------------------------------------------------------ шаг 3: исходники
    def generate_sources(self) -> None:
        """Сгенерировать синтетические исходники в каталог выгрузки."""
        out = self._cfg.media_out_path()
        self._echo(f"seed: генерация исходников в {out}")
        out.mkdir(parents=True, exist_ok=True)
        self._generate_media(out)

    def upload_sources(self, items: Mapping[str, _ItemRef]) -> None:
        """Загрузить все исходники единиц (``SOURCES_BY_DOMAIN``) и дождаться их наборов."""
        out = self._cfg.media_out_path()
        for seed_key, ref in sorted(items.items()):
            specs = SOURCES_BY_DOMAIN.get(ref.domain)
            if specs is None:
                raise SeedError(f"неизвестный домен {ref.domain!r} единицы {seed_key}")
            for spec in specs:
                path = spec.path(out, seed_key)
                if not path.is_file():
                    raise SeedError(f"нет исходника {path} для единицы {seed_key}")
            self._echo(f"seed: исходники ({len(specs)}) для {seed_key}")
            for spec in specs:
                self._upload_one(ref.media_item_id, seed_key, spec, spec.path(out, seed_key))
        for seed_key, ref in sorted(items.items()):
            self._wait_assets(ref.media_item_id, seed_key)

    def _upload_one(self, media_item_id: str, seed_key: str, spec: SourceSpec, path: Path) -> None:
        slug = seed_key.replace(":", "-")
        key = f"seed-source-{slug}-{spec.kind}-{spec.position}"
        data = {
            "media_item_id": media_item_id,
            "kind": spec.kind,
            "position": str(spec.position),
            "origin": SOURCE_ORIGIN,
            "license_note": LICENSE_NOTE,
            "reason": REASON,
        }

        def upload() -> bool:
            try:
                with path.open("rb") as fh:
                    resp = self._media.post(
                        "/admin/v1/sources",
                        headers={"Idempotency-Key": key},
                        data=data,
                        files={"file": (path.name, fh, spec.mime)},
                    )
            except httpx.TransportError as exc:
                # Соединение оборвалось (media-pipeline перезапускается): загрузка идемпотентна
                # по ключу — повторяем тем же запросом до таймаута, а не роняем засев.
                self._echo(f"seed: повтор загрузки {path.name}: {type(exc).__name__}")
                return False
            # 404 media_item.not_found: media-pipeline ещё не обработал MediaItemCreated
            # из catalog — единица появится, ждём, а не падаем.
            if resp.status_code == httpx.codes.NOT_FOUND:
                return False
            if (
                resp.status_code == httpx.codes.CONFLICT
                and self._key_reused(resp)
                and self._source_uploaded(media_item_id, spec)
            ):
                # Ключ занят другими байтами, но исходник вида и позиции уже загружен:
                # повторный засев стенда — считаем готовым.
                return True
            self._check(resp, {200, 201}, f"загрузка исходника {path.name}")
            return True

        self._poll(
            upload,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"единица {seed_key} в media-pipeline",
        )

    @staticmethod
    def _key_reused(resp: httpx.Response) -> bool:
        try:
            return bool(resp.json().get("code") == "idempotency.key_reused")
        except ValueError:
            return False

    def _source_uploaded(self, media_item_id: str, spec: SourceSpec) -> bool:
        """Есть ли у единицы исходник этого вида и позиции (``GET /admin/v1/sources``)."""
        resp = self._media.get("/admin/v1/sources", params={"media_item_id": media_item_id})
        self._check(resp, {200}, f"исходники единицы {media_item_id}")
        return any(
            s.get("kind") == spec.kind and s.get("position") == spec.position for s in resp.json()
        )

    def _wait_assets(self, media_item_id: str, seed_key: str) -> None:
        """Все наборы единицы готовы (``failed`` — сразу ошибка с типом загадки)."""

        def check() -> bool:
            resp = self._media.get("/admin/v1/asset-sets", params={"media_item_id": media_item_id})
            self._check(resp, {200}, f"наборы ассетов {seed_key}")
            sets = resp.json()
            failed = sorted(
                f"{s.get('clue_type')}/{s.get('profile_key')}"
                for s in sets
                if s["status"] == "failed"
            )
            if failed:
                raise SeedError(f"наборы ассетов единицы {seed_key} в статусе failed: {failed}")
            return bool(sets) and all(s["status"] == "ready" for s in sets)

        self._poll(
            check,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"готовность ассетов {seed_key}",
        )

    def wait_profiles(self) -> None:
        """Статические профили media-pipeline без наборов в работе и без провалов.

        Наборы текстовых загадок (``description``, ``emoji``) media-pipeline строит из событий
        catalog, а не из загрузок сидера, — их готовность видна только по профилям.
        """
        self._echo("seed: ожидание профилей media-pipeline")
        state: dict[str, Any] = {}

        def check() -> bool:
            resp = self._media.get("/admin/v1/profiles")
            self._check(resp, {200}, "профили media-pipeline")
            static = [p for p in resp.json() if p.get("origin") == "static"]
            failed = sorted(
                f"{p['clue_type']}/{p.get('profile_key')}: {p['sets']['failed']}"
                for p in static
                if p["sets"]["failed"]
            )
            if failed:
                raise SeedError(f"проваленные наборы профилей: {failed}")
            busy = [p for p in static if p["sets"]["pending"]]
            state["busy"] = sorted(f"{p['clue_type']}/{p.get('profile_key')}" for p in busy)
            return not busy

        self._poll(
            check,
            timeout_s=self._cfg.assets_timeout_s,
            what="наборы статических профилей",
            on_timeout=lambda: f"в работе: {state.get('busy')}",
        )

    # ------------------------------------------------------------------ шаг 4: пулы
    def wait_playable_pools(self, plans: Iterable[ModePlan]) -> None:
        """Дождаться играбельного пула каждого режима в проекции game.

        Ассеты готовит media-pipeline, а «играбельность» единицы появляется только после
        того, как **консьюмер game** обработает событие ``AssetsReady`` и обновит свою
        проекцию (по ``params_hash`` загадки режима и ревила). Иначе пул пуст
        (``game.pool_empty``/``game.daily_pool_too_small``), хотя ассеты в media готовы.
        """
        for plan in plans:
            self._wait_playable(plan.key, plan.min_playable)

    def _playable_ids(self, mode_key: str) -> set[str]:
        """Играбельные единицы режима: общий поиск и отдельно синтетика (лимит поиска 50).

        404 — режима нет среди опубликованных: game не опубликовал сид при старте (ошибка
        валидации пишется в лог game как ``game.bootstrap_config_invalid``), ждать бессмысленно.
        """
        found: set[str] = set()
        for q in (None, SYNTHETIC_TITLE_QUERY):
            params: dict[str, Any] = {"playable_for_mode": mode_key, "limit": PROJECTION_LIMIT}
            if q is not None:
                params["q"] = q
            resp = self._game.get("/admin/v1/projection/items", params=params)
            if resp.status_code == httpx.codes.NOT_FOUND:
                raise SeedError(
                    f"режим {mode_key} не опубликован в game: сид seeds/game/modes не прошёл "
                    "проверку при старте game (см. лог game.bootstrap_config_invalid)"
                )
            self._check(resp, {200}, f"проекция game для {mode_key}")
            found |= {str(it["media_item_id"]) for it in resp.json() if it.get("playable")}
        return found

    def _wait_playable(self, mode_key: str, min_playable: int) -> None:
        """Дождаться, пока проекция game покажет ≥ ``min_playable`` играбельных единиц режима."""
        seen: dict[str, int] = {}

        def enough() -> bool:
            seen["count"] = len(self._playable_ids(mode_key))
            return seen["count"] >= min_playable

        self._echo(f"seed: ожидание {min_playable} играбельных единиц для {mode_key}")
        self._poll(
            enough,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"играбельный пул {mode_key}",
            on_timeout=lambda: f"играбельных {seen.get('count', 0)} из {min_playable}",
        )

    # ------------------------------------------------------------------ шаг 4a: онлайн
    def wait_online_templates(self, plans: Iterable[ModePlan]) -> None:
        """Онлайн-шаблоны из сидов опубликованы: их отдаёт ``GET /v1/modes?session=online``.

        Этот же список читает фронтенд (хаб «Онлайн», «Играть с друзьями» на странице
        сборника), а match узнаёт версии шаблонов из ``game.mode_config.v1`` (ADR 0032).
        Нет онлайн-режимов в сидах — шаг пропускается.
        """
        expected = {p.key for p in plans if p.online}
        if not expected:
            return
        state: dict[str, list[str]] = {}

        def published() -> bool:
            try:
                resp = self._game_public.get("/v1/modes", params={"session": "online"})
            except httpx.TransportError:
                return False
            self._check(resp, {200}, "онлайн-шаблоны game")
            keys = {str(m.get("key")) for m in resp.json()}
            state["missing"] = sorted(expected - keys)
            return not state["missing"]

        self._echo(f"seed: ожидание онлайн-шаблонов ({len(expected)})")
        self._poll(
            published,
            timeout_s=self._cfg.assets_timeout_s,
            what="онлайн-шаблоны в game",
            on_timeout=lambda: f"нет шаблонов: {state.get('missing')}",
        )

    def online_collection_specs(
        self, plans: Iterable[ModePlan], specs: Iterable[CollectionSpec]
    ) -> list[tuple[ModePlan, CollectionSpec]]:
        """Сборники засева для онлайн-шаблонов по сборнику (см. :func:`lobby_collections`)."""
        return lobby_collections(plans, specs)

    def wait_online_collections(
        self,
        pairs: Iterable[tuple[ModePlan, CollectionSpec]],
        items: Mapping[str, _ItemRef],
    ) -> None:
        """Единицы сборника играбельны под онлайн-шаблон (``playable_for_mode`` = шаблон).

        Признак играбельности считается по ``clue.params`` шаблона лобби (D7), а не по
        режиму сборника по умолчанию — у них могут быть разные профили ассетов.
        """
        for plan, spec in pairs:
            wanted = {items[i.seed_key].media_item_id for i in spec.items}
            self._wait_collection_playable(plan, spec.key, wanted)

    def _wait_collection_playable(self, plan: ModePlan, key: str, wanted: set[str]) -> None:
        seen: dict[str, int] = {}

        def enough() -> bool:
            seen["count"] = len(wanted & self._playable_ids(plan.key))
            return seen["count"] >= plan.min_playable

        self._echo(f"seed: ожидание сборника {key} под онлайн-шаблон {plan.key}")
        self._poll(
            enough,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"сборник {key} под {plan.key}",
            on_timeout=lambda: (
                f"играбельных единиц сборника {seen.get('count', 0)} из {plan.min_playable}"
            ),
        )

    # ------------------------------------------------------------------ шаг 5: периоды
    def regenerate_period_sets(self, plans: Iterable[ModePlan]) -> None:
        """Перегенерировать набор текущего периода каждого планового режима (день, неделя).

        Набор периода создаёт периодическая задача game (``schedule_ahead_*``); пока его нет
        (404 ``game.daily_set_not_found``), ждём. 409 — набор уже заблокирован (период
        начался) — для сидера успех, если набор пригоден (см. ``_verify_period_set``).
        """
        for plan in plans:
            if plan.period is None:
                continue
            day = self._cfg.day or datetime.now(ZoneInfo(plan.timezone)).date()
            start = plan.period_start(day)
            day_iso = start.isoformat()
            self._echo(f"seed: набор периода {plan.key} ({plan.period}) на {day_iso}")
            self._verify_period_set(plan, start, self._regenerate(plan, day_iso))

    def _regenerate(self, plan: ModePlan, day_iso: str) -> httpx.Response:
        """Перегенерировать набор периода, дождавшись его создания планировщиком (404)."""
        holder: dict[str, httpx.Response] = {}

        def regenerate() -> bool:
            resp = self._game.post(
                f"/admin/v1/daily-sets/{plan.key}/{day_iso}/regenerate",
                headers={"Idempotency-Key": f"seed-period-{plan.key}-{day_iso}"},
                json={"reason": REASON},
            )
            if resp.status_code == httpx.codes.NOT_FOUND:
                return False
            self._check(resp, {200, 409}, f"набор периода {plan.key}")
            holder["resp"] = resp
            return True

        self._poll(
            regenerate,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"появление набора периода {plan.key} на {day_iso}",
        )
        return holder["resp"]

    def _verify_period_set(self, plan: ModePlan, start: date, resp: httpx.Response) -> None:
        """Убедиться, что набор периода пригоден, иначе падать здесь с внятной причиной.

        Сессия планового режима стартует только на «играбельном» наборе (``status != failed``
        и ``items``; у сетки ещё и тема ``subject_id`` и ровно ``size`` единиц); иначе
        ``POST /v1/sessions`` отвечает 503 и e2e падает на старте с непрозрачной ошибкой, а
        настоящая причина (пул не готов) остаётся скрытой. При 200 состояние — из тела ответа,
        при 409 (набор создан планировщиком) — отдельным чтением списка наборов.
        """
        day_iso = start.isoformat()
        if resp.status_code == httpx.codes.OK:
            daily = resp.json()
        else:
            got = self._game.get(
                "/admin/v1/daily-sets",
                params={"mode_key": plan.key, "from": day_iso, "to": day_iso},
            )
            self._check(got, {200}, f"состояние набора периода {plan.key}")
            daily = next((s for s in got.json() if s.get("day") == day_iso), None)
            if daily is None:
                raise SeedError(f"набор периода {plan.key} на {day_iso} не найден после 409")
        status = daily.get("status")
        items = daily.get("items") or []
        problems: list[str] = []
        if status == "failed" or not items:
            problems.append(f"status={status!r}, единиц={len(items)}")
        if plan.size is not None and items and len(items) != plan.size:
            problems.append(f"единиц {len(items)} вместо {plan.size}")
        if plan.themed and not daily.get("subject_id"):
            problems.append("нет темы subject_id")
        if problems:
            raise SeedError(
                f"набор периода {plan.key} на {day_iso} непригоден ({'; '.join(problems)}): "
                "пул игры не готов — e2e упал бы с 503 на старте сессии. "
                + self._projection_diagnostic(plan.key)
            )

    def _projection_diagnostic(self, mode_key: str) -> str:
        """Снимок проекции game для режима: что считается играбельным и с какими domain/kind.

        Набор рисуется из пула ``CandidatePort`` (``domain`` + ``pool.kinds`` + готовые
        ассеты), а признак ``playable`` в проекции — только по готовым ассетам, без фильтра
        ``kind``. Если набор получился пустым при наличии «играбельных» единиц, расхождение
        видно прямо здесь: сколько единиц проекция отдала, сколько из них playable и какие у
        них domain/kind — чтобы не лезть в логи контейнера game за причиной пустого пула.
        """
        try:
            resp = self._game.get(
                "/admin/v1/projection/items",
                params={"playable_for_mode": mode_key, "limit": PROJECTION_LIMIT},
            )
            if resp.status_code != httpx.codes.OK:
                return f"проекция: HTTP {resp.status_code}"
            rows = resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # pragma: no cover — диагностика не падает
            return f"проекцию прочитать не удалось: {exc!r}"
        playable = [r for r in rows if r.get("playable")]
        combos = sorted({f"{r.get('domain')}/{r.get('kind')}" for r in playable})
        return (
            f"проекция: {len(rows)} единиц, из них играбельных {len(playable)}; "
            f"domain/kind играбельных: {combos or '—'}"
        )

    def mode_plans(self) -> list[ModePlan]:
        """Требования ко всем режимам из сидов game (``*.yaml``)."""
        plans = [
            mode_plan(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(self._cfg.modes_path().glob("*.yaml"))
        ]
        if not any(p.period is not None for p in plans):
            raise SeedError(f"не найдены плановые режимы в {self._cfg.modes_path()}")
        return plans

    # ------------------------------------------------------------------ шаг 6: сборники
    def collection_specs(self, known_keys: Iterable[str] = ()) -> list[CollectionSpec]:
        """Сиды кураторских сборников; ключи единиц обязаны быть среди файлов импорта.

        Нет каталога ``seeds/collections`` — сборников нет (шаг пропускается).
        """
        root = self._cfg.collections_path()
        specs = [load_collection_spec(path) for path in sorted(root.glob("*.yaml"))]
        known = set(known_keys)
        seen: set[str] = set()
        for spec in specs:
            if spec.key in seen:
                raise SeedError(f"ключ сборника {spec.key!r} повторяется в {root}")
            seen.add(spec.key)
            unknown = sorted({i.seed_key for i in spec.items} - known) if known else []
            if unknown:
                raise SeedError(f"сборник {spec.key}: нет единиц в файлах импорта: {unknown}")
        return specs

    def seed_collections(
        self, specs: Iterable[CollectionSpec], items: Mapping[str, _ItemRef]
    ) -> dict[str, str]:
        """Создать или обновить сборники и дождаться их проекций; вернуть ключ → ``collection_id``.

        Сначала все мутации (проекции разных сборников догоняют параллельно), затем ожидание:
        наборы своих эмодзи со ``scope`` сборника в media-pipeline, после — режим по умолчанию
        в game (``available`` и играбельны все единицы сборника).
        """
        created = [(spec, self._upsert_collection(spec, items)) for spec in specs]
        for spec, collection_id in created:
            self._wait_scoped_assets(spec, collection_id, items)
            self._wait_collection_modes(spec, collection_id)
        return {spec.key: collection_id for spec, collection_id in created}

    def _upsert_collection(self, spec: CollectionSpec, items: Mapping[str, _ItemRef]) -> str:
        """Кураторский сборник с содержимым сида: создать, выпустить новую версию или оставить."""
        body = spec.body(items)
        digest = _content_digest({**body, "domain": spec.domain})
        existing = self._find_curated(spec.title)
        if existing is None:
            self._echo(f"seed: сборник {spec.key} — создание")
            detail = self._collection_mutation(
                spec,
                "POST",
                "/admin/v1/collections",
                key=f"seed-collection-{spec.key}-{digest}",
                payload={**body, "domain": spec.domain, "reason": REASON},
                expected={200, 201},
            )
        else:
            detail = self._admin_detail(str(existing["collection_id"]), spec.key)
            if detail.get("status") == "hidden":
                raise SeedError(
                    f"сборник {spec.key} скрыт модератором: покажите его в админке "
                    "(POST /admin/v1/collections/{id}/unhide) и повторите засев"
                )
            if _published_body(detail) == body:
                self._echo(f"seed: сборник {spec.key} — без изменений (версия {detail['version']})")
            else:
                version = int(detail["version"])
                self._echo(f"seed: сборник {spec.key} — новая версия поверх {version}")
                detail = self._collection_mutation(
                    spec,
                    "PUT",
                    f"/admin/v1/collections/{detail['collection_id']}",
                    key=f"seed-collection-{spec.key}-v{version}-{digest}",
                    payload={**body, "base_version": version, "reason": REASON},
                    expected={200},
                )
        collection_id = str(detail["collection_id"])
        self._ensure_featured(spec, detail)
        return collection_id

    def _find_curated(self, title: str) -> dict[str, Any] | None:
        """Кураторский сборник с точно таким названием (удалённые admin-список не отдаёт)."""
        resp = self._collections.get(
            "/admin/v1/collections",
            params={"q": title, "kind": "curated", "limit": ADMIN_COLLECTIONS_LIMIT},
        )
        self._check(resp, {200}, "список кураторских сборников")
        rows = [r for r in resp.json()["items"] if r.get("title") == title]
        if len(rows) > 1:
            ids = sorted(str(r["collection_id"]) for r in rows)
            raise SeedError(f"несколько кураторских сборников с названием {title!r}: {ids}")
        return rows[0] if rows else None

    def _admin_detail(self, collection_id: str, key: str) -> dict[str, Any]:
        resp = self._collections.get(f"/admin/v1/collections/{collection_id}")
        self._check(resp, {200}, f"сборник {key}")
        detail: dict[str, Any] = resp.json()
        return detail

    def _collection_mutation(
        self,
        spec: CollectionSpec,
        method: str,
        path: str,
        *,
        key: str,
        payload: Mapping[str, Any],
        expected: set[int],
    ) -> dict[str, Any]:
        """Мутация сборника с повтором, пока collections не узнает все единицы.

        422 ``collections.publish_invalid``, где все нарушения — ``item_unavailable``: catalog
        уже создал единицы, но consumer collections ещё не обработал ``MediaItemCreated`` —
        ждём. Обрыв соединения (рестарт collections) — повтор тем же ``Idempotency-Key``.
        Другие нарушения — сразу ошибка со списком ``path: code``.
        """
        holder: dict[str, Any] = {}
        waiting: dict[str, list[str]] = {}

        def attempt() -> bool:
            try:
                resp = self._collections.request(
                    method, path, headers={"Idempotency-Key": key}, json=dict(payload)
                )
            except httpx.TransportError as exc:
                self._echo(f"seed: повтор сохранения сборника {spec.key}: {type(exc).__name__}")
                return False
            if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
                errors = _publish_errors(resp)
                if errors and all(e.get("code") == ITEM_UNAVAILABLE for e in errors):
                    if not waiting:
                        self._echo(f"seed: ожидание единиц сборника {spec.key} в collections")
                    waiting["paths"] = sorted(str(e.get("path")) for e in errors)
                    return False
                if errors:
                    listed = ", ".join(f"{e.get('path')}: {e.get('code')}" for e in errors)
                    raise SeedError(f"сборник {spec.key} не проходит проверки публикации: {listed}")
            self._check(resp, expected, f"сохранение сборника {spec.key}")
            holder["detail"] = resp.json()
            return True

        self._poll(
            attempt,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"единицы сборника {spec.key} в проекции collections",
            on_timeout=lambda: f"недоступны: {waiting.get('paths')}",
        )
        detail: dict[str, Any] = holder["detail"]
        return detail

    def _ensure_featured(self, spec: CollectionSpec, detail: Mapping[str, Any]) -> None:
        """Отметка «рекомендуемое» из сида (снять чужую отметку засев не пытается)."""
        rank = spec.featured_rank
        if rank is None or (detail.get("featured") and detail.get("featured_rank") == rank):
            return
        resp = self._collections.put(
            f"/admin/v1/collections/{detail['collection_id']}/featured",
            headers={"Idempotency-Key": f"seed-featured-{spec.key}-{rank}"},
            json={"featured": True, "rank": rank, "reason": REASON},
        )
        self._check(resp, {200}, f"«рекомендуемое» сборника {spec.key}")

    def _wait_scoped_assets(
        self, spec: CollectionSpec, collection_id: str, items: Mapping[str, _ItemRef]
    ) -> None:
        """Наборы своих эмодзи сборника готовы (``AssetsReady`` со ``scope`` = сборник).

        media-pipeline строит их из ``CollectionPublished``; ``failed`` — сразу ошибка.
        """
        for item in spec.emoji_items():
            media_item_id = items[item.seed_key].media_item_id
            label = f"{spec.key}/{item.seed_key}"

            def ready(media_item_id: str = media_item_id, label: str = label) -> bool:
                try:
                    resp = self._media.get(
                        "/admin/v1/asset-sets", params={"media_item_id": media_item_id}
                    )
                except httpx.TransportError:
                    return False
                self._check(resp, {200}, f"наборы ассетов {label}")
                scoped = [
                    s
                    for s in resp.json()
                    if (s.get("scope") or {}).get("kind") == COLLECTION_SCOPE
                    and str((s.get("scope") or {}).get("id")) == collection_id
                ]
                failed = [s for s in scoped if s["status"] == "failed"]
                if failed:
                    codes = sorted(str(s.get("error_code")) for s in failed)
                    raise SeedError(f"набор своих эмодзи {label} в статусе failed: {codes}")
                return bool(scoped) and all(s["status"] == "ready" for s in scoped)

            self._echo(f"seed: ожидание своих эмодзи {label}")
            self._poll(
                ready,
                timeout_s=self._cfg.assets_timeout_s,
                what=f"набор своих эмодзи {label} (scope collection)",
            )

    def _wait_collection_modes(self, spec: CollectionSpec, collection_id: str) -> None:
        """Проекция сборника в game: режим по умолчанию доступен, играбельны все единицы.

        404 ``game.collection_not_found`` — game ещё не обработал ``CollectionPublished``.
        """
        state: dict[str, Any] = {}
        need = len(spec.items)

        def ready() -> bool:
            try:
                resp = self._game_public.get(f"/v1/collections/{collection_id}/modes")
            except httpx.TransportError:
                return False
            if resp.status_code == httpx.codes.NOT_FOUND:
                state["mode"] = "сборника нет в проекции game"
                return False
            self._check(resp, {200}, f"режимы сборника {spec.key}")
            mode = next(
                (m for m in resp.json() if m.get("mode_key") == spec.default_mode_key), None
            )
            if mode is None:
                state["mode"] = f"нет режима {spec.default_mode_key}"
                return False
            state["mode"] = (
                f"available={mode.get('available')}, играбельно {mode.get('playable')} из {need}"
            )
            return bool(mode.get("available")) and int(mode.get("playable", 0)) >= need

        self._echo(f"seed: ожидание сборника {spec.key} в game ({spec.default_mode_key})")
        self._poll(
            ready,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"сборник {spec.key} в проекции game",
            on_timeout=lambda: str(state.get("mode", "нет ответа")),
        )

    # ------------------------------------------------------------------ шаг 8: progression
    def seed_progression(self) -> None:
        """Предметы, паки и конфиг с товаром «своя аватарка» (5b, ADR 0045-0048).

        Порядок важен. Конфиг v1 (с секцией ``shop``, но ``custom_avatar_item_key = null``)
        публикует задача ``progression.config.bootstrap``: публикация с ключом требует, чтобы
        предмет уже существовал (422 ``progression.config_invalid``). Поэтому: ждём
        опубликованный конфиг (слоты и редкости предметов проверяются по нему) -> предметы
        (создание, картинка, активация) -> паки -> следующая версия конфига с ключом товара.
        Всё идемпотентно по ключу предмета/пака и по версии конфига. Нет ``items/items.yaml`` -
        шаг пропускается.
        """
        root = self._cfg.progression_path()
        items_file = root / "items" / "items.yaml"
        if not items_file.is_file():
            self._echo("seed: сидов progression нет, шаг пропущен")
            return
        spec = _mapping(yaml.safe_load(items_file.read_text(encoding="utf-8")))
        item_specs = [_mapping(i) for i in spec.get("items") or []]
        packs_file = root / "items" / "packs.yaml"
        pack_specs: list[dict[str, Any]] = []
        if packs_file.is_file():
            packs_raw = _mapping(yaml.safe_load(packs_file.read_text(encoding="utf-8")))
            pack_specs = [_mapping(p) for p in packs_raw.get("packs") or []]
        avatar_key = spec.get("custom_avatar_item_key")
        self._echo("seed: progression - ожидание опубликованного конфига")
        _, body = self._wait_published_config()
        if not isinstance(body.get("shop"), dict):
            raise SeedError(
                "в опубликованной версии нет секции shop: перезапустите progression "
                "с актуальным seeds/progression/config.yaml"
            )
        by_key = {str(i["key"]): i for i in self._progression_items()}
        for item_spec in item_specs:
            by_key[str(item_spec["key"])] = self._ensure_item(item_spec, by_key, root)
        self._ensure_packs(pack_specs, by_key)
        if isinstance(avatar_key, str):
            self._ensure_avatar_config(body, avatar_key, by_key)

    # ------------------------------------------------------------------ шаг 8b: события
    def seed_events(self) -> None:
        """Определения сезонных событий из ``seeds/progression/events/*.yaml`` (5d, ADR 0054).

        Файл: ``key``, ``body`` (по ``event_definition.v1.json``), необязательный ``map_image``
        (путь относительно папки событий; нет файла - карта не грузится). Создаётся только
        определение: запуски (период, публикация) заводит владелец в админке, поэтому событие
        остаётся без запусков. Повторный запуск идемпотентен: совпавшее тело не трогаем,
        изменённое заменяем (``PUT``), если нет запуска ``published/active`` (иначе сервер
        ответил бы 409 ``event_definition_locked``); карту грузим, пока у события её нет.
        Предмет-награда должен существовать - шаг идёт после :meth:`seed_progression`.
        Нет папки событий - шаг пропускается.
        """
        root = self._cfg.progression_path() / "events"
        files = sorted(root.glob("*.yaml")) if root.is_dir() else []
        if not files:
            self._echo("seed: событий progression нет, шаг пропущен")
            return
        for path in files:
            try:
                raw = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
                key, body = str(raw["key"]), _mapping(raw["body"])
                map_image = str(raw["map_image"]) if raw.get("map_image") else None
            except (KeyError, TypeError, yaml.YAMLError) as exc:
                raise SeedError(f"сид события {path.name} некорректен: {exc!r}") from exc
            event = self._ensure_event(key, body)
            if map_image and not event.get("map_url"):
                self._upload_event_map(key, root / map_image)

    def _ensure_event(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        """Событие с определением из сида: создать, заменить тело или оставить как есть."""
        digest = _content_digest(body)
        got = self._progression.get(f"/admin/v1/events/{key}")
        if got.status_code == httpx.codes.NOT_FOUND:
            self._echo(f"seed: событие {key} - создание")
            resp = self._progression.post(
                "/admin/v1/events",
                headers={"Idempotency-Key": f"seed-event-create-{key}-{digest}"},
                json={"key": key, "body": body, "reason": REASON},
            )
            self._check(resp, {201}, f"создание события {key}")
            return _mapping(resp.json())
        self._check(got, {200}, f"событие {key}")
        event = _mapping(got.json())
        if event.get("body") == body:
            return event
        runs = event.get("runs") or []
        if any(r.get("status") in LOCKING_RUN_STATUSES for r in runs):
            self._echo(f"seed: событие {key} - есть опубликованный запуск, определение не меняю")
            return event
        self._echo(f"seed: событие {key} - обновление определения")
        resp = self._progression.put(
            f"/admin/v1/events/{key}",
            headers={"Idempotency-Key": f"seed-event-update-{key}-{digest}"},
            json={"body": body, "reason": REASON},
        )
        self._check(resp, {200}, f"обновление события {key}")
        return _mapping(resp.json())

    def _upload_event_map(self, key: str, path: Path) -> None:
        """Картинка карты события (PNG/WebP); нет файла - пропуск."""
        if not path.is_file():
            self._echo(f"seed: карта события {key}: нет файла {path.name}, пропуск")
            return
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:12]
        mime = "image/png" if path.suffix.lower() == ".png" else "image/webp"
        resp = self._progression.put(
            f"/admin/v1/events/{key}/map",
            headers={"Idempotency-Key": f"seed-event-map-{key}-{digest}"},
            files={"file": (path.name, data, mime)},
            data={"reason": REASON},
        )
        self._check(resp, {200}, f"карта события {key}")

    # ------------------------------------------------------------------ шаг 9: social
    def seed_social(self) -> None:
        """Дождаться опубликованного конфига social (5c, ADR 0053).

        Конфиг v1 из ``seeds/social/config.yaml`` публикует задача ``social.config.bootstrap``
        (только local/ci); без него ручки social отвечают 503 ``social.config_unavailable``.
        Шаг ничего не создаёт: игроки e2e регистрируются сами, справочник причин и порог жалоб
        - часть конфига. Нет сидов social - шаг пропускается.
        """
        if not (self._cfg.repo_root / "seeds" / "social" / "config.yaml").is_file():
            self._echo("seed: сидов social нет, шаг пропущен")
            return
        self._echo("seed: social - ожидание опубликованного конфига")

        def published() -> bool:
            resp = self._social.get("/admin/v1/config/versions")
            self._check(resp, {200}, "версии конфига social")
            return any(c.get("status") == "published" for c in resp.json().get("items", []))

        self._poll(
            published,
            timeout_s=self._cfg.assets_timeout_s,
            what="опубликованный конфиг social (social.config.bootstrap)",
        )

    def _wait_published_config(self) -> tuple[int, dict[str, Any]]:
        """Дождаться опубликованной версии конфига (bootstrap) и вернуть ``(версия, тело)``."""
        holder: dict[str, Any] = {}

        def published() -> bool:
            resp = self._progression.get("/admin/v1/configs")
            self._check(resp, {200}, "версии конфига progression")
            cards = [c for c in resp.json().get("items", []) if c.get("status") == "published"]
            if not cards:
                return False
            version = int(cards[0]["version"])
            detail = self._progression.get(f"/admin/v1/configs/{version}")
            self._check(detail, {200}, f"конфиг progression v{version}")
            holder["version"] = version
            holder["config"] = _mapping(detail.json()["config"])
            return True

        self._poll(
            published,
            timeout_s=self._cfg.assets_timeout_s,
            what="опубликованный конфиг progression (progression.config.bootstrap)",
        )
        return int(holder["version"]), holder["config"]

    def _progression_items(self) -> list[dict[str, Any]]:
        """Все предметы админки (страницы по курсору)."""
        found: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            resp = self._progression.get("/admin/v1/items", params=params)
            self._check(resp, {200}, "список предметов progression")
            page = resp.json()
            found.extend(_mapping(i) for i in page.get("items", []))
            cursor = page.get("next_cursor")
            if not cursor:
                return found

    def _ensure_item(
        self, item_spec: Mapping[str, Any], by_key: Mapping[str, dict[str, Any]], root: Path
    ) -> dict[str, Any]:
        """Предмет существует и с картинкой; новый предмет активируется.

        Сид только добавляет: существующий предмет с изменёнными в yaml полями НЕ обновляется
        (правка - через админку). Активация - только предмету, которому сид сам только что
        загрузил картинку: предмет, деактивированный админом, повторно не включается, а
        создание/активация прерванного прогона повторяются теми же ключами идемпотентности.
        """
        key = str(item_spec["key"])
        fields = {
            name: item_spec.get(name)
            for name in ("key", "slot", "rarity", "title", "media_kind", "level_required", "price")
        }
        if item_spec.get("event_key"):
            fields["event_key"] = item_spec["event_key"]
        digest = hashlib.sha256(
            json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
        item = by_key.get(key)
        if item is None:
            resp = self._progression.post(
                "/admin/v1/items",
                headers={"Idempotency-Key": f"seed-item-create-{key}-{digest}"},
                json={**fields, "active": False, "reason": REASON},
            )
            if resp.status_code == httpx.codes.CONFLICT:
                # Параллельный запуск или прежний сид без записи в by_key: берём созданное.
                item = next((i for i in self._progression_items() if i["key"] == key), None)
                if item is None:
                    self._check(resp, {201}, f"создание предмета {key}")
            else:
                self._check(resp, {201}, f"создание предмета {key}")
                item = _mapping(resp.json())
            self._echo(f"seed: предмет {key} создан")
        assert item is not None  # noqa: S101 - _check выше бросает при неудаче
        uploaded = not item.get("asset_url")
        if uploaded:
            item = self._upload_item_media(key, item, root)
        if uploaded and item_spec.get("active") and not item.get("active"):
            resp = self._progression.put(
                f"/admin/v1/items/{item['id']}",
                headers={"Idempotency-Key": f"seed-item-activate-{key}-{digest}"},
                json={**fields, "active": True, "reason": REASON},
            )
            self._check(resp, {200}, f"активация предмета {key}")
            item = _mapping(resp.json())
        return item

    def _upload_item_media(self, key: str, item: Mapping[str, Any], root: Path) -> dict[str, Any]:
        path = root / "items" / "images" / f"{key}.webp"
        if not path.is_file():
            raise SeedError(
                f"нет картинки {path}: uv run python seeds/progression/generate_items.py"
            )
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:12]
        resp = self._progression.put(
            f"/admin/v1/items/{item['id']}/media",
            headers={"Idempotency-Key": f"seed-item-media-{key}-{digest}"},
            files={"file": (path.name, data, "image/webp")},
            data={"reason": REASON},
        )
        self._check(resp, {200}, f"картинка предмета {key}")
        return _mapping(resp.json())

    def _ensure_packs(
        self, pack_specs: Iterable[Mapping[str, Any]], by_key: Mapping[str, dict[str, Any]]
    ) -> None:
        """Паки по ключу: существующий не трогаем (правка пака - через админку)."""
        specs = list(pack_specs)
        if not specs:
            return
        resp = self._progression.get("/admin/v1/packs")
        self._check(resp, {200}, "список паков progression")
        existing = {str(p["key"]) for p in resp.json().get("items", [])}
        for pack in specs:
            key = str(pack["key"])
            if key in existing:
                continue
            missing = [k for k in pack["item_keys"] if k not in by_key]
            if missing:
                raise SeedError(f"пак {key}: нет предметов {missing}")
            body = {
                "key": key,
                "title": pack["title"],
                "style": pack["style"],
                "item_ids": [by_key[k]["id"] for k in pack["item_keys"]],
                "verses_amount": pack.get("verses_amount", 0),
                "price_kopecks": pack["price_kopecks"],
                "active": bool(pack.get("active", False)),
                "reason": REASON,
            }
            created = self._progression.post(
                "/admin/v1/packs", headers={"Idempotency-Key": f"seed-pack-{key}"}, json=body
            )
            self._check(created, {200, 201}, f"создание пака {key}")
            self._echo(f"seed: пак {key} создан")

    def _ensure_avatar_config(
        self, body: Mapping[str, Any], avatar_key: str, by_key: Mapping[str, Any]
    ) -> None:
        """Опубликовать версию конфига с ``shop.custom_avatar_item_key``.

        Ключ ставится только в ``null``: уже заданный (в том числе админом) не трогаем. Чужой
        черновик администратора (не равный ни опубликованному телу, ни нашему) не
        перезаписывается - пропуск с логом. Ключи идемпотентности содержат хеш тела, поэтому
        прерванный прогон (черновик сохранён, публикация упала) повторяется теми же ключами.
        При 409 ``draft_changed`` черновик перечитывается и публикация повторяется один раз.
        """
        shop = body["shop"]
        current = shop.get("custom_avatar_item_key")
        if current == avatar_key:
            return
        if current is not None:
            self._echo(f"seed: пропуск: ключ задан админом ({current!r}), конфиг не меняю")
            return
        if avatar_key not in by_key:
            raise SeedError(f"товар своей аватарки {avatar_key!r} не заведён")
        config = {**body, "shop": {**shop, "custom_avatar_item_key": avatar_key}}
        draft = self._progression_draft()
        if draft is not None and draft[1] not in (config, body):
            self._echo("seed: пропуск: в progression есть черновик админа, конфиг не меняю")
            return
        sha = hashlib.sha256(
            json.dumps(config, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
        saved = self._progression.put(
            "/admin/v1/configs/draft",
            headers={"Idempotency-Key": f"seed-config-draft-{sha}"},
            json={"config": config, "reason": REASON},
        )
        self._check(saved, {200, 201}, "черновик конфига progression")
        card = saved.json()["card"]
        published = self._publish_draft(card, sha, "")
        if published.status_code == httpx.codes.CONFLICT and _code(published) == DRAFT_CHANGED:
            reread = self._progression_draft()
            if reread is None or reread[1] != config:
                self._echo("seed: пропуск: черновик изменён админом, публикацию не повторяю")
                return
            published = self._publish_draft(reread[0], sha, "-retry")
        self._check(published, {200, 201}, "публикация конфига progression")
        self._echo(f"seed: конфиг progression v{card['version']} с товаром {avatar_key}")

    def _publish_draft(self, card: Mapping[str, Any], sha: str, suffix: str) -> httpx.Response:
        return self._progression.post(
            "/admin/v1/configs/draft/publish",
            headers={"Idempotency-Key": f"seed-config-publish-{sha}{suffix}"},
            json={"version": card["version"], "body_sha256": card["body_sha256"], "reason": REASON},
        )

    def _progression_draft(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Текущий черновик конфига: ``(карточка, тело)`` или ``None``."""
        resp = self._progression.get("/admin/v1/configs")
        self._check(resp, {200}, "версии конфига progression")
        drafts = [c for c in resp.json().get("items", []) if c.get("status") == "draft"]
        if not drafts:
            return None
        detail = self._progression.get(f"/admin/v1/configs/{drafts[0]['version']}")
        self._check(detail, {200}, "черновик конфига progression")
        data = detail.json()
        return _mapping(data["card"]), _mapping(data["config"])

    # ------------------------------------------------------------------ вспомогательное
    def _expected_keys(self) -> set[str]:
        """Ключи единиц из файлов импорта (``key`` каждой строки JSONL)."""
        keys: set[str] = set()
        for file_id in self._present_import_files():
            keys |= _jsonl_keys(self._cfg.catalog_path() / file_id)
        if not keys:
            raise SeedError(f"нет файлов импорта в {self._cfg.catalog_path()}")
        return keys

    def _present_import_files(self) -> list[str]:
        root = self._cfg.catalog_path()
        return [name for name in IMPORT_FILES if (root / name).is_file()]

    def _check(self, resp: httpx.Response, expected: set[int], what: str) -> None:
        if resp.status_code in expected:
            return
        detail = ""
        try:
            body = resp.json()
            code = body.get("code") or body.get("detail")
            detail = f" ({code})" if code else ""
        except ValueError:
            detail = ""
        raise SeedError(f"{what}: неожиданный ответ {resp.status_code}{detail}")

    def _poll(
        self,
        check: Callable[[], bool],
        *,
        timeout_s: float,
        what: str,
        on_timeout: Callable[[], str] | None = None,
    ) -> None:
        """Опрашивать ``check`` до истины или до таймаута (без бесконечного цикла)."""
        deadline = time.monotonic() + timeout_s
        while True:
            if check():
                return
            if time.monotonic() >= deadline:
                extra = f": {on_timeout()}" if on_timeout else ""
                raise SeedError(f"таймаут ожидания ({what}){extra}")
            time.sleep(self._cfg.poll_interval_s)


# Код 409 публикации: тело черновика изменилось после сохранения (progression).
DRAFT_CHANGED = "progression.draft_changed"


def _code(resp: httpx.Response) -> str | None:
    """Машинный ``code`` ответа-проблемы; не JSON - ``None``."""
    try:
        code = resp.json().get("code")
    except (ValueError, AttributeError):
        return None
    return str(code) if code else None


def _publish_errors(resp: httpx.Response) -> list[dict[str, Any]]:
    """Нарушения ``collections.publish_invalid`` (``details.errors``); иное — пустой список."""
    try:
        body = resp.json()
    except ValueError:
        return []
    if not isinstance(body, dict) or body.get("code") != "collections.publish_invalid":
        return []
    errors = (body.get("details") or {}).get("errors") or []
    return [e for e in errors if isinstance(e, dict)]


def _jsonl_keys(path: Path) -> set[str]:
    """Ключи ``key`` из строк JSONL (пустые строки и строки с ``#`` пропускаются)."""
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        row = json.loads(line)
        if "key" in row:
            keys.add(str(row["key"]))
    return keys


def run_seed(config: SeedConfig, **kwargs: Any) -> None:
    """Выполнить сидирование с заданной конфигурацией (закрывает клиенты)."""
    with Seeder(config, **kwargs) as seeder:
        seeder.run()
