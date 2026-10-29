"""Пересоздание наборов ассетов после правки профиля (``gv dev replan-assets``) — только local.

Правка ``asset_profiles.yaml`` меняет ``params_hash`` загадки: у уже загруженных единиц нет
наборов под новый хэш. Команда одним вызовом:

1. входит в админку (как ``gv dev seed``);
2. по каждому статическому профилю типа загадки и каждому его домену порциями вызывает
   ``POST /admin/v1/asset-sets/replan`` (media-pipeline планирует только недостающие наборы,
   старые остаются — по ним доигрываются начатые сессии и наборы дня);
3. ждёт, пока наборы статических профилей будут готовы (``GET /admin/v1/profiles``);
4. ждёт, пока game выпустит новые версии режимов из ``seeds/game/modes`` с этим типом загадки
   (``game.modes.bootstrap`` публикует правку сида, когда под новый хэш готов пул).

Повторный запуск безопасен: готовые наборы не трогаются, версия режима выходит один раз.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import yaml

from gv_cli.seed import REASON, SeedConfig, Seeder, SeedError

REPLAN_BATCH = 200


class Replanner(Seeder):
    """Шаги пересоздания; аутентификация и опрос — из :class:`Seeder`."""

    def replan(self, clue_type: str, *, batch: int = REPLAN_BATCH) -> dict[str, int]:
        """Поставить недостающие наборы статических профилей ``clue_type``; итог по доменам."""
        resp = self._media.get("/admin/v1/profiles", params={"clue_type": clue_type})
        self._check(resp, {200}, "профили media-pipeline")
        domains = sorted(
            {d for p in resp.json() if p.get("origin") == "static" for d in p.get("domains", [])}
        )
        if not domains:
            raise SeedError(f"нет статических профилей типа загадки {clue_type}")
        run_id = uuid.uuid4().hex  # ключи идемпотентности — на запуск: повтор команды не «залипает»
        scheduled: dict[str, int] = {}
        for domain in domains:
            after: str | None = None
            total = 0
            while True:
                body = {
                    "clue_type": clue_type,
                    "domain": domain,
                    "limit": batch,
                    "after": after,
                    "reason": REASON,
                }
                resp = self._media.post(
                    "/admin/v1/asset-sets/replan",
                    json=body,
                    headers={"Idempotency-Key": f"replan-{run_id}-{domain}-{after or 'start'}"},
                )
                self._check(resp, {200}, f"пересоздание наборов {clue_type}/{domain}")
                data = resp.json()
                total += int(data["scheduled"])
                after = data["next_after"]
                if after is None:
                    break
            scheduled[domain] = total
            self._echo(f"replan: {clue_type}/{domain} — поставлено наборов: {total}")
        return scheduled

    def mode_seeds(self, clue_type: str) -> dict[str, dict[str, Any]]:
        """Сиды режимов game с этим типом загадки: ключ → ``clue.params``."""
        found: dict[str, dict[str, Any]] = {}
        for path in sorted(self._cfg.modes_path().glob("*.yaml")):
            body = yaml.safe_load(path.read_text(encoding="utf-8"))
            clue = body.get("clue") or {}
            if clue.get("type") == clue_type:
                found[str(body["key"])] = clue.get("params") or {}
        return found

    def wait_modes(self, clue_type: str) -> None:
        """Дождаться, пока последняя версия каждого режима совпадёт с сидом."""
        wanted = self.mode_seeds(clue_type)
        if not wanted:
            return
        self._echo(f"replan: ожидание новых версий режимов ({len(wanted)})")
        waiting: list[str] = []

        def check() -> bool:
            waiting.clear()
            waiting.extend(
                key for key, params in wanted.items() if not self._mode_current(key, params)
            )
            return not waiting

        self._poll(
            check,
            timeout_s=self._cfg.assets_timeout_s,
            what="новые версии режимов в game",
            on_timeout=lambda: f"ещё не обновлены: {sorted(waiting)}",
        )

    def _mode_current(self, key: str, params: dict[str, Any]) -> bool:
        resp = self._game.get(f"/admin/v1/mode-configs/{key}")
        self._check(resp, {200}, f"режим {key}")
        version = resp.json().get("published_version")
        if version is None:
            return False
        resp = self._game.get(f"/admin/v1/mode-configs/{key}/versions/{version}")
        self._check(resp, {200}, f"версия {version} режима {key}")
        published = ((resp.json().get("body") or {}).get("clue") or {}).get("params")
        return bool(published == params)


def run_replan(
    config: SeedConfig,
    *,
    clue_type: str,
    batch: int = REPLAN_BATCH,
    wait_modes: bool = True,
    echo: Callable[[str], None] = print,
) -> dict[str, int]:
    """Пересоздать наборы профиля и дождаться готовности; вернуть число наборов по доменам."""
    seeder = Replanner(config, echo=echo)
    try:
        seeder.authenticate()
        scheduled = seeder.replan(clue_type, batch=batch)
        seeder.wait_profiles()
        if wait_modes:
            seeder.wait_modes(clue_type)
    finally:
        seeder.close()
    echo("replan: готово")
    return scheduled
