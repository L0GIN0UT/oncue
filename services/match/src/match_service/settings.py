"""Настройки match (переменные окружения ``GV_*``, план этапа 4 §2.7).

Игровые величины (таймеры, очки, раунды) — только в конфиге режима; здесь — лимиты и
параметры процесса ``rooms``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gv_service import ServiceSettings, UgcSettings

_REDIS_SOCKET_TIMEOUT_MS = 2000
"""``socket_timeout`` клиента Redis процесса (``Infrastructure.open``): блокирующие вызовы
(``BLPOP`` ответа, ``XREAD`` потока) должны укладываться в него."""


class MatchSettings(BaseModel):
    """``GV_MATCH__*``.

    Attributes:
        lease_ttl_s: TTL lease владельца комнаты (D7).
        lease_renew_s: Продление lease.
        reply_timeout_ms: Сколько api ждёт ответа владельца, затем 202 (D6).
        tick_s: Период heartbeat клиента (D10).
        presence_timeout_s: Без heartbeat дольше — игрок «Отключён».
        max_rooms_per_instance: Комнат на один процесс ``rooms``.
        lobby_create_per_user_per_hour: Создание комнат пользователем.
        join_by_code_per_ip_per_min: Поиск комнаты по коду с одного IP (fail-closed).
        commands_per_player_per_s: Команды игрока в секунду.
        chat_per_player_per_min: Сообщения чата игрока в минуту.
        chat_slow_interval_s: Санкция ``CHAT_SLOW``: не чаще одного сообщения за интервал.
        realtime_tokens_per_user_per_min: Выдача subscription-токенов.
        rate_per_user_per_hour: Оценки игроков после матча.
        room_idle_close_min: Простой лобби и показ итога до закрытия комнаты.
        op_window: Сколько последних итогов команд помнит комната на игрока (повтор ``op_id``).
        poison_limit: Сбоев обработки комнаты подряд до её прерывания (``internal_error``).
        tick_per_player_per_min: Heartbeat игрока в минуту (без WebSocket — раз в секунду).
        state_per_player_per_min: ``GET /state`` игрока в минуту (ресинк).
        join_by_code_per_user_per_min: Поиск комнаты по коду пользователем (fail-closed).
        emote_per_player_per_min: Эмоции и фразы игрока в минуту.
        create_lock_ms: Замок от параллельного создания комнат одним игроком.
        memory_limit: Сколько сыгранных единиц помнит лобби (``exclude_ids`` ≤ 5000).
        rooms_poll_ms: Максимальная пауза цикла владельца (таймеры, присутствие).
        orphan_scan_s: Проверка комнат без владельца (захват после падения).
        snapshot_ttl_h: TTL снимка активной комнаты (страховка от вечных ключей).
        closed_ttl_s: Сколько хранить снимок закрытой комнаты.
        reply_ttl_s: TTL списка ответа ``{room:CODE}:reply:*``.
        stream_maxlen: Предел длины потока команд комнаты.
        game_url: Адрес game (план раундов, тело конфига).
        game_audience: ``aud`` сервисного токена для game.
        game_timeout_s: Таймаут вызова game (старт ждёт его не дольше).
        media_url: Адрес media-pipeline.
        media_audience: ``aud`` сервисного токена для media-pipeline.
        media_timeout_s: Таймаут выдачи ссылки.
        breaker_failures: Отказов подряд до размыкания breaker-а.
        breaker_cooldown_s: Пауза разомкнутого breaker-а.
        partitions_ahead_months: На сколько месяцев вперёд создавать партиции раундов.
        partitions_interval_s: Период задачи партиций.
    """

    model_config = ConfigDict(frozen=True)

    lease_ttl_s: float = Field(default=10, gt=0, le=120)
    lease_renew_s: float = Field(default=3, gt=0, le=60)
    reply_timeout_ms: int = Field(default=1500, ge=50, le=1800)
    tick_s: int = Field(default=5, ge=1, le=60)
    presence_timeout_s: int = Field(default=12, ge=2, le=120)
    max_rooms_per_instance: int = Field(default=300, ge=1, le=10000)
    lobby_create_per_user_per_hour: int = Field(default=20, ge=1)
    join_by_code_per_ip_per_min: int = Field(default=30, ge=1)
    commands_per_player_per_s: int = Field(default=10, ge=1)
    chat_per_player_per_min: int = Field(default=20, ge=1)
    chat_slow_interval_s: int = Field(default=10, ge=1)
    realtime_tokens_per_user_per_min: int = Field(default=30, ge=1)
    rate_per_user_per_hour: int = Field(default=60, ge=1)
    room_idle_close_min: float = Field(default=30, gt=0)
    op_window: int = Field(default=32, ge=4, le=1024)
    poison_limit: int = Field(default=3, ge=1, le=100)
    tick_per_player_per_min: int = Field(default=150, ge=1)
    state_per_player_per_min: int = Field(default=60, ge=1)
    join_by_code_per_user_per_min: int = Field(default=20, ge=1)
    emote_per_player_per_min: int = Field(default=30, ge=1)
    create_lock_ms: int = Field(default=5000, ge=100, le=60_000)
    memory_limit: int = Field(default=2000, ge=0, le=5000)
    rooms_poll_ms: int = Field(default=250, ge=5, le=1500)
    orphan_scan_s: float = Field(default=2, gt=0, le=60)
    snapshot_ttl_h: float = Field(default=24, gt=0)
    closed_ttl_s: int = Field(default=600, ge=1)
    reply_ttl_s: int = Field(default=30, ge=1)
    stream_maxlen: int = Field(default=2000, ge=100)
    game_url: str = "http://game:8000"
    game_audience: str = "game"
    game_timeout_s: float = Field(default=1.2, gt=0, le=30)
    media_url: str = "http://media-pipeline:8000"
    media_audience: str = "media_pipeline"
    media_timeout_s: float = Field(default=1.0, gt=0, le=30)
    breaker_failures: int = Field(default=5, ge=1)
    breaker_cooldown_s: float = Field(default=15, gt=0)
    partitions_ahead_months: int = Field(default=3, ge=1, le=24)
    partitions_interval_s: float = Field(default=21600, gt=0)

    @model_validator(mode="after")
    def _timings(self) -> MatchSettings:
        if self.lease_renew_s * 2 > self.lease_ttl_s:
            raise ValueError("GV_MATCH__LEASE_RENEW_S должен быть не больше половины LEASE_TTL_S")
        if self.rooms_poll_ms >= _REDIS_SOCKET_TIMEOUT_MS:
            raise ValueError("GV_MATCH__ROOMS_POLL_MS должен быть меньше socket_timeout Redis")
        return self


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "match"
    match: MatchSettings = MatchSettings()
    ugc: UgcSettings = UgcSettings()
