/**
 * Тайминги клиента realtime (ADR 0035, D10 плана этапа 4). Это параметры транспорта, а не
 * игровые величины: таймеры фаз приходят с сервера (`phase_ends_at`). Значения по умолчанию
 * совпадают с `GV_MATCH__TICK_S` match; тесты передают свои.
 */
export type RealtimeConfig = {
  /** Период tick при живом WebSocket (heartbeat присутствия), мс. */
  tickMs: number;
  /** Период tick без WebSocket (HTTP-stream или канал недоступен): tick — фолбэк снимка, мс. */
  tickFallbackMs: number;
  /** Без связи (ни tick, ни канала) дольше — баннер «нет связи», мс. */
  offlineAfterMs: number;
  /** После 202 `CommandAccepted` ждём дельту столько, затем ресинк по REST, мс. */
  acceptedResyncMs: number;
  /** Сколько сообщений с опережением версии держать до ресинка. */
  bufferLimit: number;
};

export const DEFAULT_REALTIME_CONFIG: RealtimeConfig = {
  tickMs: 5_000,
  tickFallbackMs: 1_000,
  offlineAfterMs: 20_000,
  acceptedResyncMs: 1_500,
  bufferLimit: 100,
};
