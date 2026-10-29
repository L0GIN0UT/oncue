# ruff: noqa: PLR2004 - границы полей повторяют event_definition.v1.json (лимиты схемы)
"""Редактор находок формой: тело определения события <-> поля формы.

Чистые функции без Django (проверяют unit-тесты). Источник истины остаётся за progression:
проверки здесь повторяют ``event_definition.v1.json`` и ``domain.events_season`` только ради
ранних подсказок у поля. Тело, которого форма не знает (лишние поля определения, находки и
триггеров), берётся из ``base`` - исходного тела, с которым форма открыта, и сохраняется как
есть: форма меняет только то, что показывает.
"""

from __future__ import annotations

import copy
import math
import re
import uuid
from collections.abc import Mapping
from typing import Any

# Перечни - как в contracts/progression/event_definition.v1.json (дрейф ловит unit-тест).
METRICS = (
    "rounds_played",
    "correct_answers",
    "wins",
    "online_matches",
    "daily_completed",
    "collection_published",
    "daily_streak",
    "duel_win",
    "weekly_top_place",
    "wheel_spin_streak",
    "item_equipped",
    "collection_played",
)
PERIODS = ("day", "event")
PLAY_PAGES = (
    "home",
    "daily",
    "solo",
    "online",
    "collections",
    "leaderboards",
    "wheel",
    "shop",
    "profile",
)
SPOT_PAGES = ("home", "leaderboards", "profile", "collections", "match_summary", "shop", "wheel")
DOMAINS = ("music", "film")
TRIGGER_TYPES = ("ui_spot", "server")

MAX_FINDS = 20
MIN_FINDS = 2
MAX_ROWS = 50  # сколько строк форма вообще разбирает (защита от раздутого POST)
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_I18N_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
_THEME_RE = re.compile(r"^([a-z][a-z0-9_-]*)?$")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SLOT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_LIST_SPLIT = re.compile(r"[\s,;]+")

ROW_FIELDS = (
    "src",
    "key",
    "title",
    "task_i18n",
    "diary_before",
    "diary_after",
    "x_pct",
    "y_pct",
    "xp",
    "verses",
    "play_page",
    "trigger_type",
    "page_key",
    "metric",
    "target",
    "period",
    "place_max",
    "slot",
    "item_key",
    "domain",
    "collection_keys",
    "collection_tags",
)
HEADER_FIELDS = ("title", "description", "theme_key", "reward_item_key")
_PARAM_KEYS = (
    "place_max",
    "slot",
    "item_key",
    "domain",
    "collection_keys",
    "collection_tags",
)

Row = dict[str, Any]


def pct_from_click(offset: float, size: float) -> float:
    """Доля картинки в процентах по смещению клика: 0..100, один знак после запятой.

    Та же формула в ``static/admin_events/map_picker.js`` (округление «половина вверх»).
    """
    if size <= 0:
        return 0.0
    value = min(max(offset / size * 100.0, 0.0), 100.0)
    return math.floor(value * 10 + 0.5) / 10


# ---------------------------------------------------------------------------- тело -> поля


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_text(value: Any) -> str:
    return "\n".join(str(v) for v in value) if isinstance(value, list) else ""


def header_from_body(body: Mapping[str, Any]) -> dict[str, str]:
    """Поля шапки определения."""
    return {
        "title": _text(body.get("title")),
        "description": _text(body.get("description")),
        "theme_key": _text(body.get("theme_key")),
        "reward_item_key": _text(_dict(body.get("reward")).get("item_key")),
    }


def row_from_find(find: Any, src: int) -> Row:
    """Строка формы из находки; чужие/битые значения - пустыми полями."""
    f = _dict(find)
    trigger = _dict(f.get("trigger"))
    params = _dict(trigger.get("params"))
    reward = _dict(f.get("reward"))
    row: Row = dict.fromkeys(ROW_FIELDS, "")
    row |= {
        "src": str(src),
        "key": _text(f.get("key")),
        "title": _text(f.get("title")),
        "task_i18n": _text(f.get("task_i18n")),
        "diary_before": _text(f.get("diary_before")),
        "diary_after": _text(f.get("diary_after")),
        "x_pct": _text(f.get("x_pct")),
        "y_pct": _text(f.get("y_pct")),
        "xp": _text(reward.get("xp")),
        "verses": _text(reward.get("verses")),
        "play_page": _text(f.get("play_page")),
        "trigger_type": "server" if trigger.get("type") == "server" else "ui_spot",
        "page_key": _text(trigger.get("page_key")),
        "metric": _text(trigger.get("metric")),
        "target": _text(trigger.get("target")),
        "period": _text(trigger.get("period")),
        "place_max": _text(params.get("place_max")),
        "slot": _text(params.get("slot")),
        "item_key": _text(params.get("item_key")),
        "domain": _text(params.get("domain")),
        "collection_keys": _list_text(params.get("collection_keys")),
        "collection_tags": _list_text(params.get("collection_tags")),
        "final": bool(f.get("final")),
    }
    return row


def rows_from_body(body: Mapping[str, Any]) -> list[Row]:
    finds = body.get("finds")
    return [row_from_find(f, i) for i, f in enumerate(finds)] if isinstance(finds, list) else []


def blank_row(number: int) -> Row:
    """Новая находка: ui_spot на главной, без наград."""
    row = row_from_find({}, -1)
    row |= {
        "src": "",
        "key": f"find_{number}",
        "x_pct": "50",
        "y_pct": "50",
        "xp": "0",
        "verses": "0",
        "trigger_type": "ui_spot",
        "page_key": "home",
        "final": False,
    }
    return row


# ---------------------------------------------------------------------------- POST -> поля


def parse_post(data: Mapping[str, Any]) -> tuple[dict[str, str], list[Row]]:
    """Шапка и строки из POST (``QueryDict`` или dict); число строк - ``find_count``."""
    header = {name: str(data.get(f"h_{name}", "")).strip() for name in HEADER_FIELDS}
    try:
        count = int(str(data.get("find_count", "0")))
    except ValueError:
        count = 0
    count = min(max(count, 0), MAX_ROWS)
    final_raw = str(data.get("final_index", ""))
    rows: list[Row] = []
    for i in range(count):
        row: Row = {name: str(data.get(f"f{i}_{name}", "")).strip() for name in ROW_FIELDS}
        for text_name in ("title", "diary_before", "diary_after"):
            row[text_name] = str(data.get(f"f{i}_{text_name}", "")).strip()
        row["trigger_type"] = row["trigger_type"] or "ui_spot"
        row["final"] = final_raw == str(i)
        rows.append(row)
    return header, rows


def apply_op(rows: list[Row], op: str) -> list[Row]:
    """Операция над списком: ``add``, ``del:i``, ``up:i``, ``down:i``; иное - без изменений."""
    result = list(rows)
    if op == "add":
        if len(result) < MAX_FINDS:
            result.append(blank_row(len(result) + 1))
        return result
    name, _, raw = op.partition(":")
    if name not in ("del", "up", "down") or not raw.isdigit():
        return result
    i = int(raw)
    if not 0 <= i < len(result):
        return result
    if name == "del":
        del result[i]
    elif name == "up" and i > 0:
        result[i - 1], result[i] = result[i], result[i - 1]
    elif name == "down" and i < len(result) - 1:
        result[i + 1], result[i] = result[i], result[i + 1]
    return result


# ---------------------------------------------------------------------------- поля -> тело


def _number(raw: str) -> Any:
    """Число из поля; не число - исходная строка (серверная проверка покажет ошибку)."""
    text = raw.strip().replace(",", ".")
    try:
        return int(text)
    except ValueError:
        pass
    try:
        value = float(text)
    except ValueError:
        return raw
    return int(value) if value.is_integer() else value


def pct_value(raw: str) -> float | None:
    """Число из поля координаты; не число - ``None``."""
    value = _number(raw)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _split(raw: str) -> list[str]:
    return [item for item in _LIST_SPLIT.split(raw.strip()) if item]


def _trigger(row: Row, base: dict[str, Any]) -> dict[str, Any]:
    kind = row["trigger_type"]
    # Неизвестные поля сохраняются, пока тип триггера не меняется.
    trigger = copy.deepcopy(base) if base.get("type") == kind else {}
    trigger["type"] = kind
    if kind == "ui_spot":
        trigger["page_key"] = row["page_key"]
        return trigger
    trigger["metric"] = row["metric"]
    trigger["target"] = _number(row["target"])
    trigger["period"] = row["period"]
    params = {k: v for k, v in _dict(trigger.get("params")).items() if k not in _PARAM_KEYS}
    if row["place_max"]:
        params["place_max"] = _number(row["place_max"])
    for name in ("slot", "item_key", "domain"):
        if row[name]:
            params[name] = row[name]
    for name in ("collection_keys", "collection_tags"):
        if _split(row[name]):
            params[name] = _split(row[name])
    if params:
        trigger["params"] = params
    else:
        trigger.pop("params", None)
    return trigger


def _find(row: Row, number: int, base: dict[str, Any]) -> dict[str, Any]:
    find = copy.deepcopy(base)
    find |= {
        "key": row["key"],
        "number": number,
        "title": row["title"],
        "diary_before": row["diary_before"],
        "diary_after": row["diary_after"],
        "x_pct": _number(row["x_pct"]),
        "y_pct": _number(row["y_pct"]),
        "reward": {
            **_dict(base.get("reward")),
            "xp": _number(row["xp"]),
            "verses": _number(row["verses"]),
        },
        "final": bool(row.get("final")),
    }
    if row["task_i18n"]:
        find["task_i18n"] = row["task_i18n"]
    else:
        find.pop("task_i18n", None)
    if row["play_page"]:
        find["play_page"] = row["play_page"]
    else:
        find.pop("play_page", None)
    find["trigger"] = _trigger(row, _dict(base.get("trigger")))
    return find


def order_rows(rows: list[Row]) -> list[Row]:
    """Финальная находка - последняя (остальные сохраняют порядок)."""
    return [r for r in rows if not r.get("final")] + [r for r in rows if r.get("final")]


def build_body(
    base: Mapping[str, Any], header: Mapping[str, str], rows: list[Row]
) -> dict[str, Any]:
    """Тело определения: ``base`` + значения формы. Номера - по порядку, финальная - последняя."""
    body = copy.deepcopy(dict(base))
    body["title"] = header["title"]
    body["description"] = header["description"]
    body["theme_key"] = header["theme_key"]
    body["reward"] = {**_dict(body.get("reward")), "item_key": header["reward_item_key"] or None}
    base_finds = base.get("finds")
    finds: list[dict[str, Any]] = []
    for number, row in enumerate(order_rows(rows), start=1):
        src = row.get("src", "")
        origin: dict[str, Any] = {}
        if (
            isinstance(base_finds, list)
            and str(src).isdigit()
            and int(src) < len(base_finds)
            and isinstance(base_finds[int(src)], dict)
        ):
            origin = base_finds[int(src)]
        finds.append(_find(row, number, origin))
    body["finds"] = finds
    return body


# ---------------------------------------------------------------------------- проверка


def _int_in(raw: str, low: int, high: int) -> bool:
    return raw.strip().lstrip("+-").isdigit() and low <= int(raw) <= high


def _num_in(raw: str, low: float, high: float) -> bool:
    value = _number(raw)
    return isinstance(value, int | float) and low <= value <= high


def _is_uuid(raw: str) -> bool:
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def validate_header(header: Mapping[str, str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not 1 <= len(header["title"]) <= 120:
        errors["h_title"] = "название: 1-120 символов"
    if len(header["description"]) > 2000:
        errors["h_description"] = "описание: до 2000 символов"
    if len(header["theme_key"]) > 32 or not _THEME_RE.fullmatch(header["theme_key"]):
        errors["h_theme_key"] = "тема: a-z, цифры, _ и -, начинается с буквы, до 32 символов"
    if len(header["reward_item_key"]) > 64:
        errors["h_reward_item_key"] = "ключ предмета: до 64 символов"
    return errors


def _trigger_errors(row: Row) -> dict[str, str]:  # noqa: PLR0912 - перечень правил
    errors: dict[str, str] = {}
    if row["trigger_type"] == "ui_spot":
        if row["page_key"] not in SPOT_PAGES:
            errors["page_key"] = "выберите страницу"
        return errors
    if row["trigger_type"] != "server":
        errors["trigger_type"] = "тип триггера: ui_spot или server"
        return errors
    metric = row["metric"]
    if metric not in METRICS:
        errors["metric"] = "выберите метрику"
    if not _int_in(row["target"], 1, 10000):
        errors["target"] = "цель: целое 1-10000"
    if row["period"] not in PERIODS:
        errors["period"] = "выберите период"
    if row["domain"] and row["domain"] not in DOMAINS:
        errors["domain"] = "домен: music или film"
    if metric == "weekly_top_place":
        if not _int_in(row["place_max"], 1, 100):
            errors["place_max"] = "место: целое 1-100 (обязательно для weekly_top_place)"
    elif row["place_max"]:
        errors["place_max"] = "только для weekly_top_place"
    if metric == "item_equipped":
        if bool(row["slot"]) == bool(row["item_key"]):
            errors["slot"] = "укажите ровно одно: слот или ключ предмета"
        if row["target"] and row["target"] != "1":
            errors["target"] = "надеть предмет - цель 1"
        if row["slot"] and not _SLOT_RE.fullmatch(row["slot"]):
            errors["slot"] = "слот: a-z, цифры, _"
    elif row["slot"] or row["item_key"]:
        errors["slot"] = "слот и предмет - только для item_equipped"
    keys, tags = _split(row["collection_keys"]), _split(row["collection_tags"])
    if (keys or tags) and metric != "collection_played":
        errors["collection_keys"] = "сборники и теги - только для collection_played"
    elif len(keys) > 20 or len(set(keys)) != len(keys) or not all(map(_is_uuid, keys)):
        errors["collection_keys"] = "до 20 уникальных UUID"
    if (
        tags
        and metric == "collection_played"
        and (len(tags) > 20 or len(set(tags)) != len(tags) or not all(map(_TAG_RE.fullmatch, tags)))
    ):
        errors["collection_tags"] = "до 20 уникальных тегов: a-z, цифры, _, до 32 символов"
    return errors


def _row_errors(row: Row) -> dict[str, str]:
    errors: dict[str, str] = {}
    if len(row["key"]) > 64 or not _KEY_RE.fullmatch(row["key"]):
        errors["key"] = "ключ: a-z, цифры, _, начинается с буквы, до 64 символов"
    if not 1 <= len(row["title"]) <= 120:
        errors["title"] = "название: 1-120 символов"
    if row["task_i18n"] and not (
        len(row["task_i18n"]) <= 200 and _I18N_RE.fullmatch(row["task_i18n"])
    ):
        errors["task_i18n"] = (
            "ключ i18n: a-z, цифры, _, части через точку (event.name.task), до 200 символов"
        )
    for name in ("diary_before", "diary_after"):
        if len(row[name]) > 1000:
            errors[name] = "до 1000 символов"
    for name in ("x_pct", "y_pct"):
        if not _num_in(row[name], 0, 100):
            errors[name] = "число 0-100 (доля изображения, %)"
    if not _int_in(row["xp"], 0, 5000):
        errors["xp"] = "XP: целое 0-5000"
    if not _int_in(row["verses"], 0, 1000):
        errors["verses"] = "Версы: целое 0-1000"
    if row["play_page"] and row["play_page"] not in PLAY_PAGES:
        errors["play_page"] = "неизвестный раздел"
    return errors | _trigger_errors(row)


def validate(header: Mapping[str, str], rows: list[Row]) -> dict[str, str]:
    """Ошибки по именам полей формы (``h_title``, ``f0_key``); общие - ключ ``finds``."""
    errors = validate_header(header)
    if not MIN_FINDS <= len(rows) <= MAX_FINDS:
        errors["finds"] = f"находок должно быть от {MIN_FINDS} до {MAX_FINDS}"
    if sum(1 for r in rows if r.get("final")) != 1:
        errors["finds"] = "отметьте ровно одну финальную находку"
    seen: set[str] = set()
    for i, row in enumerate(rows):
        for name, message in _row_errors(row).items():
            errors[f"f{i}_{name}"] = message
        if row["key"] in seen:
            errors[f"f{i}_key"] = "ключ повторяется"
        seen.add(row["key"])
    return errors


# ---------------------------------------------------------------------------- ошибки сервера

_PATH_FIND = re.compile(r"^finds(?:\[(\d*)\]|\.(\d+))\.?(.*)$")
_FIELD_BY_TAIL = {
    "trigger.type": "trigger_type",
    "trigger.page_key": "page_key",
    "trigger.metric": "metric",
    "trigger.target": "target",
    "trigger.period": "period",
    "reward.xp": "xp",
    "reward.verses": "verses",
}


def server_field(path: str, shown_order: list[int]) -> str | None:
    """Имя поля формы по пути ошибки progression (``finds.2.x_pct``, ``finds[1].trigger.target``).

    ``shown_order[n]`` - позиция строки формы для n-й находки отправленного тела (финальная
    переехала в конец). ``None`` - ошибка не про конкретное поле.
    """
    if path in ("title", "description", "theme_key"):
        return f"h_{path}"
    if path in ("reward.item_key", "reward"):
        return "h_reward_item_key"
    match = _PATH_FIND.match(path)
    if not match:
        return None
    raw_index = match.group(1) or match.group(2)
    if not raw_index or int(raw_index) >= len(shown_order):
        return None
    tail = match.group(3)
    name = _FIELD_BY_TAIL.get(tail)
    if name is None:
        if tail.startswith("trigger.params."):
            name = tail.removeprefix("trigger.params.")
        elif tail.startswith("trigger"):
            name = "metric"
        else:
            name = tail
    if name not in ROW_FIELDS:
        return None
    return f"f{shown_order[int(raw_index)]}_{name}"


def sent_order(rows: list[Row]) -> list[int]:
    """Для каждой отправленной находки - позиция её строки в форме."""
    indexed = list(enumerate(rows))
    return [i for i, r in indexed if not r.get("final")] + [i for i, r in indexed if r.get("final")]
