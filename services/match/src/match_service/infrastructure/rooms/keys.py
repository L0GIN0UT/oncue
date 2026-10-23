"""Ключи Redis комнат (D6, D7). Ключи одной комнаты — с hash-tag ``{room:CODE}``.

Все ключи комнаты попадают в один слот Redis Cluster: Lua-CAS снимка трогает владельца и
снимок атомарно. Индексы (``rooms:*``) и ``player:{id}:room`` — в своих слотах, их правка
не атомарна с комнатой и не обязана быть: истину хранит снимок.
"""

from __future__ import annotations

from uuid import UUID

ACTIVE = "rooms:active"
"""ZSET активных комнат (score — время создания, мс): подбор сирот и админка."""
OPEN = "rooms:open"
"""ZSET публичных лобби со свободными местами (score — время создания, мс)."""
ORPHANS = "rooms:orphans"
"""LIST кодов комнат без владельца (новые и отданные при handoff)."""


def tag(code: str) -> str:
    """Hash-tag комнаты."""
    return "{room:" + code + "}"


def snapshot(code: str) -> str:
    """Снимок комнаты (JSON)."""
    return f"{tag(code)}:snap"


def owner(code: str) -> str:
    """Lease владельца ``instance|epoch``."""
    return f"{tag(code)}:owner"


def epoch(code: str) -> str:
    """Счётчик fencing-эпох."""
    return f"{tag(code)}:epoch"


def commands(code: str) -> str:
    """Поток команд (Redis Stream)."""
    return f"{tag(code)}:cmd"


def reply(code: str, user_id: UUID, op_id: str) -> str:
    """Список ответа на команду (``BLPOP`` в api)."""
    return f"{tag(code)}:reply:{user_id}:{op_id}"


def seen(code: str) -> str:
    """HASH heartbeat игроков: ``user_id → мс``."""
    return f"{tag(code)}:seen"


def player(user_id: UUID) -> str:
    """Активная комната игрока."""
    return f"player:{user_id}:room"


def creating(user_id: UUID) -> str:
    """Игрок прямо сейчас создаёт комнату (замок от параллельных созданий)."""
    return f"player:{user_id}:creating"


def create_idempotency(user_id: UUID, key: str) -> str:
    """Ключ ``Idempotency-Key`` создания комнаты."""
    return f"match:idem:create:{user_id}:{key}"


def room_keys(code: str) -> list[str]:
    """Все ключи комнаты (истечение после закрытия)."""
    return [snapshot(code), owner(code), epoch(code), commands(code), seen(code), spectators(code)]


# ----------------------------------------------------------------------------- очереди подбора
# Все ключи очередей — в одном hash-слоте ``{mmq}``: Lua-скрипты трогают запись игрока,
# ZSET очереди и lease атомарно (очередей четыре, нагрузка мала — ADR 0038).
QUEUE_TAG = "{mmq}"


def queue_name(ladder: str, fmt: str) -> str:
    """Имя очереди ``(ladder, format)``."""
    return f"{ladder}:{fmt}"


def queue_zset_prefix() -> str:
    """Префикс ZSET очередей (score — рейтинг, member — ``user_id``)."""
    return f"{QUEUE_TAG}:q:"


def queue_zset(name: str) -> str:
    """ZSET очереди."""
    return f"{queue_zset_prefix()}{name}"


def queue_player(user_id: UUID) -> str:
    """HASH записи игрока в очереди."""
    return f"{QUEUE_TAG}:p:{user_id}"


def queue_owner(name: str) -> str:
    """Lease матчера очереди ``instance|epoch``."""
    return f"{QUEUE_TAG}:lease:{name}"


def queue_epoch(name: str) -> str:
    """Счётчик fencing-эпох очереди."""
    return f"{QUEUE_TAG}:epoch:{name}"


def queue_unused(name: str) -> str:
    """Заглушка ключа снимка (lease очереди снимков не пишет)."""
    return f"{QUEUE_TAG}:nosnap:{name}"


def queue_age(name: str) -> str:
    """ZSET очереди по времени входа (score — ``entered_at``, мс): старейшие первыми в батче."""
    return f"{QUEUE_TAG}:t:{name}"


def queue_reserved(name: str) -> str:
    """ZSET резервов пар (score — момент резерва, мс): зачистка после падения матчера."""
    return f"{QUEUE_TAG}:r:{name}"


def spectators(code: str) -> str:
    """SET наблюдателей комнаты (отписка по окончании матча)."""
    return f"{tag(code)}:spectators"
