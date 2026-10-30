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
        room_name_max_length: Предел длины названия комнаты от хоста (≤ 100 — предел схемы).
        join_by_code_per_ip_per_min: Поиск комнаты по коду с одного IP (fail-closed).
        commands_per_player_per_s: Команды игрока в секунду.
        chat_per_player_per_min: Сообщения чата игрока в минуту.
        chat_slow_interval_s: Санкция ``CHAT_SLOW``: не чаще одного сообщения за интервал.
        unsubscribe_attempts: Попытки отзыва подписки Centrifugo (кик, выход, санкция ONLINE).
        unsubscribe_retry_s: Пауза между попытками отзыва подписки (растёт линейно).
        revocation_workers: Воркеры фонового отзыва подписок/соединений Centrifugo (на процесс).
        revocation_queue_size: Ёмкость очереди отзыва; переполнение - метрика и лог, не блок.
        revocation_drain_s: Сколько ждать очередь отзыва при остановке (SIGTERM), остаток сброс.
        realtime_tokens_per_user_per_min: Выдача subscription-токенов.
        rate_per_user_per_hour: Оценки игроков после матча.
        room_idle_close_min: Простой лобби и показ итога до закрытия комнаты.
        empty_lobby_close_s: Лобби без онлайн-игроков закрывается владельцем через столько
            секунд (0 - выключено); не зависит от шины и Centrifugo.
        op_window: Сколько последних итогов команд помнит комната на игрока (повтор ``op_id``).
        poison_limit: Сбоев обработки комнаты подряд до её прерывания (``internal_error``).
        tick_per_player_per_min: Heartbeat игрока в минуту (без WebSocket — раз в секунду).
        state_per_player_per_min: ``GET /state`` игрока в минуту (ресинк).
        join_by_code_per_user_per_min: Поиск комнаты по коду пользователем (fail-closed).
        emote_per_player_per_min: Эмоции и фразы игрока в минуту.
        create_lock_ms: Замок от параллельного создания комнат одним игроком.
        pending_room_ms: Свежий указатель ``player:{id}:room`` на ещё не созданную комнату
            считается занятым (создание или вход в процессе).
        op_users_max: Сколько игроков максимум хранят окно ``op_id`` в снимке комнаты.
        memory_limit: Сколько сыгранных единиц помнит лобби (``exclude_ids`` ≤ 5000).
        command_reader_backoff_max_s: Потолок паузы перезапуска общего читателя команд (ADR 0063).
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
        default_start_rating: Рейтинг новичка, пока проекция не знает сезона со сбросом (в
            событиях rating нет ``elo.start``; обычно ``soft_reset.target`` ladder-а).
        queue_ttl_s: Запись в очереди без heartbeat (``POST /queue/tick``) исчезает через
            столько секунд; не меньше двух ``tick_s`` (один пропуск не выбивает из очереди).
        queue_matched_ttl_s: Сколько живёт запись игрока после подбора (идёт в комнату).
        queue_reserved_ttl_s: Жизнь записи с зарезервированной парой, пока создаётся комната
            (короче ``queue_matched_ttl_s``; ``tick`` её продлевает).
        queue_reserved_stale_s: Резерв старше этого срока считается брошенным упавшим матчером:
            игрок возвращается в поиск (меньше ``queue_reserved_ttl_s``).
        rate_window_s: Окно лимитов очереди и наблюдения (лимиты названы «в минуту»).
        queue_batch_max: Игроков очереди в одном батче матчера.
        matcher_poll_ms: Пауза цикла матчера (периоды очередей — ``interval_s`` ladder-а).
        matcher_lease_ttl_s: TTL lease очереди (fencing-эпоха, как у комнат).
        ladder_cache_s: Кэш проекции ladder-ов в процессе.
        queue_enter_per_user_per_min: Входов игрока в очередь в минуту.
        queue_tick_per_user_per_min: Heartbeat очереди игрока в минуту.
        spectate_per_user_per_min: Запросы наблюдателя (список, снимок, токен) в минуту.
        spectate_scan_pages: Сколько страниц индекса комнат просматривает список наблюдения.
    """

    model_config = ConfigDict(frozen=True)

    lease_ttl_s: float = Field(default=10, gt=0, le=120)
    lease_renew_s: float = Field(default=3, gt=0, le=60)
    reply_timeout_ms: int = Field(default=1500, ge=50, le=1800)
    tick_s: int = Field(default=5, ge=1, le=60)
    presence_timeout_s: int = Field(default=12, ge=2, le=120)
    max_rooms_per_instance: int = Field(default=300, ge=1, le=10000)
    lobby_create_per_user_per_hour: int = Field(default=20, ge=1)
    room_name_max_length: int = Field(default=40, ge=1, le=100)
    join_by_code_per_ip_per_min: int = Field(default=30, ge=1)
    commands_per_player_per_s: int = Field(default=10, ge=1)
    chat_per_player_per_min: int = Field(default=20, ge=1)
    chat_slow_interval_s: int = Field(default=10, ge=1)
    unsubscribe_attempts: int = Field(default=3, ge=1, le=10)
    unsubscribe_retry_s: float = Field(default=0.2, ge=0, le=5)
    revocation_workers: int = Field(default=4, ge=1, le=64)
    revocation_queue_size: int = Field(default=1000, ge=1, le=100_000)
    revocation_drain_s: float = Field(default=5, ge=0, le=60)
    realtime_tokens_per_user_per_min: int = Field(default=30, ge=1)
    rate_per_user_per_hour: int = Field(default=60, ge=1)
    room_idle_close_min: float = Field(default=30, gt=0)
    empty_lobby_close_s: int = Field(default=120, ge=0, le=86_400)
    op_window: int = Field(default=32, ge=4, le=1024)
    poison_limit: int = Field(default=3, ge=1, le=100)
    tick_per_player_per_min: int = Field(default=150, ge=1)
    state_per_player_per_min: int = Field(default=60, ge=1)
    join_by_code_per_user_per_min: int = Field(default=20, ge=1)
    emote_per_player_per_min: int = Field(default=30, ge=1)
    create_lock_ms: int = Field(default=5000, ge=100, le=60_000)
    op_users_max: int = Field(default=16, ge=2, le=256)
    pending_room_ms: int = Field(default=10_000, ge=1000, le=120_000)
    memory_limit: int = Field(default=2000, ge=0, le=5000)
    command_reader_backoff_max_s: float = Field(default=5, gt=0, le=60)
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
    default_start_rating: int = Field(default=1000, ge=0)
    queue_ttl_s: float = Field(default=20, gt=0, le=600)
    queue_matched_ttl_s: float = Field(default=120, gt=0, le=3600)
    queue_reserved_ttl_s: float = Field(default=30, gt=0, le=600)
    queue_reserved_stale_s: float = Field(default=10, gt=0, le=600)
    rate_window_s: int = Field(default=60, ge=1, le=3600)
    queue_batch_max: int = Field(default=2000, ge=2, le=20_000)
    matcher_poll_ms: int = Field(default=250, ge=5, le=1500)
    matcher_lease_ttl_s: float = Field(default=10, gt=0, le=120)
    ladder_cache_s: float = Field(default=5, ge=0, le=300)
    queue_enter_per_user_per_min: int = Field(default=20, ge=1)
    queue_tick_per_user_per_min: int = Field(default=150, ge=1)
    spectate_per_user_per_min: int = Field(default=60, ge=1)
    spectate_scan_pages: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _timings(self) -> MatchSettings:
        if self.lease_renew_s * 2 > self.lease_ttl_s:
            raise ValueError("GV_MATCH__LEASE_RENEW_S должен быть не больше половины LEASE_TTL_S")
        if 0 < self.empty_lobby_close_s < self.presence_timeout_s:
            raise ValueError(
                "GV_MATCH__EMPTY_LOBBY_CLOSE_S не должен быть меньше PRESENCE_TIMEOUT_S"
            )
        if self.rooms_poll_ms >= _REDIS_SOCKET_TIMEOUT_MS:
            raise ValueError("GV_MATCH__ROOMS_POLL_MS должен быть меньше socket_timeout Redis")
        if self.matcher_poll_ms >= _REDIS_SOCKET_TIMEOUT_MS:
            raise ValueError("GV_MATCH__MATCHER_POLL_MS должен быть меньше socket_timeout Redis")
        if self.queue_ttl_s < self.tick_s * 2:
            raise ValueError("GV_MATCH__QUEUE_TTL_S должен быть не меньше двух TICK_S")
        if not self.queue_reserved_stale_s < self.queue_reserved_ttl_s < self.queue_matched_ttl_s:
            raise ValueError(
                "нужно QUEUE_RESERVED_STALE_S < QUEUE_RESERVED_TTL_S < QUEUE_MATCHED_TTL_S"
            )
        if self.matcher_poll_ms / 1000 * 2 > self.matcher_lease_ttl_s:
            raise ValueError("GV_MATCH__MATCHER_LEASE_TTL_S слишком мал для MATCHER_POLL_MS")
        return self


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "match"
    match: MatchSettings = MatchSettings()
    ugc: UgcSettings = UgcSettings()
