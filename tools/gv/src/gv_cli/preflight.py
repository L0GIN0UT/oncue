"""Проверки окружения перед запуском стека (``gv dev up``).

Секреты Centrifugo обязаны различаться: connection-токены подписывает identity, subscription-токены
комнат — match (разделение D9, ADR 0034). Если оба HMAC-секрета совпадают, токен подписки
проходил бы как connection-токен и наоборот. Значения секретов никогда не попадают в сообщения.
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations
from pathlib import Path

# Переменные, значения которых должны быть попарно разными и непустыми.
DISTINCT_SECRETS = (
    "CENTRIFUGO_TOKEN_HMAC_SECRET",
    "CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET",
    "CENTRIFUGO_API_KEY",
)


class PreflightError(Exception):
    """Окружение не готово к запуску; сообщение не содержит значений секретов."""


def read_env_file(path: Path) -> dict[str, str]:
    """Разобрать ``KEY=VALUE`` (комментарии и пустые строки пропускаются); нет файла — пусто."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def check_centrifugo_secrets(env: Mapping[str, str]) -> None:
    """Секреты Centrifugo заданы и попарно различны.

    Raises:
        PreflightError: пустой или совпадающий секрет (названы переменные, не значения).
    """
    present = {name: env.get(name, "") for name in DISTINCT_SECRETS}
    empty = [name for name, value in present.items() if not value]
    if empty:
        raise PreflightError("не заданы секреты Centrifugo: " + ", ".join(empty))
    same = [(a, b) for a, b in combinations(DISTINCT_SECRETS, 2) if present[a] == present[b]]
    if same:
        pairs = "; ".join(f"{a} = {b}" for a, b in same)
        raise PreflightError(
            f"секреты Centrifugo совпадают ({pairs}): разделение connection/subscription (D9) "
            "теряет смысл. Сгенерируйте разные значения (gv dev secrets)."
        )


def check_env(env_file: Path, environ: Mapping[str, str]) -> None:
    """Все проверки окружения; значения из процесса перекрывают ``.env`` (как у compose)."""
    check_centrifugo_secrets({**read_env_file(env_file), **environ})
