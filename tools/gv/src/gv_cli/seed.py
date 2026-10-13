"""Оркестратор ``gv dev seed`` — наполнение запущенного стека для e2e-сценария ``daily``.

Только для local/ci: сидер работает на хосте и обращается к admin/internal-ручкам сервисов
напрямую (в dev/ci они опубликованы на 127.0.0.1 через ``compose.services.dev.yml``; на
gateway этих ручек нет). Шаги идемпотентны и безопасны при повторном запуске: все мутации
несут стабильный ``Idempotency-Key`` и ``reason``, а ожидание готовности — опрос с таймаутом.

Последовательность (см. ``seeds/catalog/README.md`` и ``seeds/media/README.md``):

1. Аутентификация: сервисный токен (client credentials, ``audience=identity``) → вход в
   админку (``/internal/v1/admin-sessions``) → полученный admin-токен идёт ``Bearer`` во все
   ``/admin/v1/*``.
2. Импорт каталога из файлов (``films.jsonl``, ``tracks.jsonl``) с ожиданием завершения
   задания и появления единиц в списке.
3. Генерация синтетических исходников и загрузка по одному на единицу
   (``audio`` для музыки, ``frame`` для фильмов) с ожиданием готовности наборов ассетов.
4. Перегенерация наборов дня для каждого дейлик-режима (``source.type: daily``) на нужный день.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
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
IMPORT_FILES = ("films.jsonl", "tracks.jsonl")
# Часовой пояс дейликов по умолчанию, если в сиде режима не задан ``source.params.timezone``.
DEFAULT_TIMEZONE = "Europe/Moscow"
# Вид исходника media-pipeline (``SourceKind``) по домену единицы каталога.
_KIND_BY_DOMAIN = {"music": "audio", "film": "frame"}
# Расширение синтетического исходника по его виду (``seeds/media/generate.py``).
_EXT_BY_KIND = {"audio": ".m4a", "frame": ".jpg"}
_MIME_BY_KIND = {"audio": "audio/mp4", "frame": "image/jpeg"}

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
        game_url: Базовый адрес game (admin-ручки).
        admin_login: Логин администратора (e-mail или никнейм).
        admin_password: Пароль администратора.
        service_client_id: Идентификатор сервисного клиента (по умолчанию ``admin``).
        service_secret: Сырой секрет сервисного клиента.
        repo_root: Корень монорепозитория (для запуска генератора и поиска сидов).
        day: День наборов дня; ``None`` — сегодня в часовом поясе режима.
        catalog_dir: Каталог файлов импорта (по умолчанию ``seeds/catalog``).
        media_out_dir: Куда генератор кладёт исходники (по умолчанию ``seeds/media/out``).
        modes_dir: Сиды режимов game (по умолчанию ``seeds/game/modes``).
        http_timeout_s: Таймаут одного HTTP-запроса.
        poll_interval_s: Пауза между опросами готовности.
        import_timeout_s: Предел ожидания завершения импорта и появления единиц.
        assets_timeout_s: Предел ожидания готовности наборов ассетов.
    """

    identity_url: str
    catalog_url: str
    media_url: str
    game_url: str
    admin_login: str
    admin_password: str
    service_secret: str
    repo_root: Path
    service_client_id: str = "admin"
    day: date | None = None
    catalog_dir: Path | None = None
    media_out_dir: Path | None = None
    modes_dir: Path | None = None
    http_timeout_s: float = 30.0
    poll_interval_s: float = 1.0
    import_timeout_s: float = 60.0
    assets_timeout_s: float = 120.0

    def catalog_path(self) -> Path:
        """Каталог файлов импорта."""
        return self.catalog_dir or self.repo_root / "seeds" / "catalog"

    def media_out_path(self) -> Path:
        """Каталог синтетических исходников."""
        return self.media_out_dir or self.repo_root / "seeds" / "media" / "out"

    def modes_path(self) -> Path:
        """Каталог сидов режимов."""
        return self.modes_dir or self.repo_root / "seeds" / "game" / "modes"


@dataclass(slots=True)
class _ItemRef:
    """Единица каталога, сопоставленная сидером."""

    media_item_id: str
    domain: str


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
        self._admin_token: str | None = None

    def close(self) -> None:
        """Закрыть все HTTP-клиенты."""
        for client in (self._identity, self._catalog, self._media, self._game):
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
        self.import_catalog()
        items = self.resolve_items(expected)
        self.generate_sources()
        self.upload_sources(items)
        self.regenerate_daily_sets()
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
        # admin-токен — Bearer для всех admin-ручек каталога, медиа и игры.
        for client in (self._catalog, self._media, self._game):
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
        """Загрузить по одному исходнику на единицу и дождаться готовности наборов."""
        out = self._cfg.media_out_path()
        for seed_key, ref in sorted(items.items()):
            kind = _KIND_BY_DOMAIN.get(ref.domain)
            if kind is None:
                raise SeedError(f"неизвестный домен {ref.domain!r} единицы {seed_key}")
            slug = seed_key.replace(":", "-")
            path = out / f"{slug}{_EXT_BY_KIND[kind]}"
            if not path.is_file():
                raise SeedError(f"нет исходника {path} для единицы {seed_key}")
            self._echo(f"seed: исходник {kind} для {seed_key}")
            self._upload_one(ref.media_item_id, kind, slug, path)
        for seed_key, ref in sorted(items.items()):
            self._wait_assets(ref.media_item_id, seed_key)

    def _upload_one(self, media_item_id: str, kind: str, slug: str, path: Path) -> None:
        with path.open("rb") as fh:
            resp = self._media.post(
                "/admin/v1/sources",
                headers={"Idempotency-Key": f"seed-source-{slug}"},
                data={"media_item_id": media_item_id, "kind": kind, "reason": REASON},
                files={"file": (path.name, fh, _MIME_BY_KIND[kind])},
            )
        self._check(resp, {200, 201}, f"загрузка исходника {slug}")

    def _wait_assets(self, media_item_id: str, seed_key: str) -> None:
        def check() -> bool:
            resp = self._media.get("/admin/v1/asset-sets", params={"media_item_id": media_item_id})
            self._check(resp, {200}, f"наборы ассетов {seed_key}")
            sets = resp.json()
            if any(s["status"] == "failed" for s in sets):
                raise SeedError(f"набор ассетов единицы {seed_key} в статусе failed")
            return bool(sets) and any(s["status"] == "ready" for s in sets)

        self._poll(
            check,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"готовность ассетов {seed_key}",
        )

    # ------------------------------------------------------------------ шаг 4: дейлики
    def regenerate_daily_sets(self) -> None:
        """Перегенерировать набор дня для каждого дейлик-режима на нужный день.

        Ассеты готовит media-pipeline, а «играбельность» единицы появляется только после
        того, как **консьюмер game** обработает событие ``AssetsReady`` и обновит свою
        проекцию. Поэтому перед перегенерацией ждём, пока проекция game покажет достаточно
        играбельных единиц, — иначе пул пуст (``game.daily_pool_too_small``) и набор дня
        получается пустым, несмотря на готовые в media-pipeline ассеты.
        """
        for mode_key, timezone, min_playable in self._daily_modes():
            self._wait_playable(mode_key, min_playable)
            day = self._cfg.day or datetime.now(ZoneInfo(timezone)).date()
            day_iso = day.isoformat()
            self._echo(f"seed: набор дня {mode_key} на {day_iso}")
            resp = self._game.post(
                f"/admin/v1/daily-sets/{mode_key}/{day_iso}/regenerate",
                headers={"Idempotency-Key": f"seed-daily-{mode_key}-{day_iso}"},
                json={"reason": REASON},
            )
            # 409 — набор уже создан периодической задачей/заблокирован: для сидера это успех.
            self._check(resp, {200, 409}, f"набор дня {mode_key}")

    def _wait_playable(self, mode_key: str, min_playable: int) -> None:
        """Дождаться, пока проекция game покажет ≥ ``min_playable`` играбельных единиц режима."""

        def enough() -> bool:
            resp = self._game.get(
                "/admin/v1/projection/items",
                params={"playable_for_mode": mode_key, "limit": 50},
            )
            self._check(resp, {200}, f"проекция game для {mode_key}")
            return sum(1 for it in resp.json() if it.get("playable")) >= min_playable

        self._echo(f"seed: ожидание {min_playable} играбельных единиц для {mode_key}")
        self._poll(
            enough,
            timeout_s=self._cfg.assets_timeout_s,
            what=f"играбельный пул {mode_key}",
        )

    def _daily_modes(self) -> list[tuple[str, str, int]]:
        """Ключ режима, часовой пояс и минимальный играбельный пул для ``source.type: daily``."""
        modes: list[tuple[str, str, int]] = []
        for path in sorted(self._cfg.modes_path().glob("*.yaml")):
            spec = yaml.safe_load(path.read_text(encoding="utf-8"))
            source = spec.get("source", {})
            if source.get("type") != "daily":
                continue
            params = source.get("params", {})
            timezone = params.get("timezone", DEFAULT_TIMEZONE)
            # min_playable из пула; запасной вариант — размер набора (нужно хотя бы на set).
            min_playable = int(params.get("pool", {}).get("min_playable", params.get("size", 1)))
            modes.append((str(spec["key"]), str(timezone), min_playable))
        if not modes:
            raise SeedError(f"не найдены дейлик-режимы в {self._cfg.modes_path()}")
        return modes

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
