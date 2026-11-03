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
  /**
   * Сразу после входа снимок может ещё не знать игрока (вход ответил 202, владелец не записал
   * снимок): 403/404 до первого снимка повторяются столько раз с паузой `entryRetryMs`.
   */
  entryRetries: number;
  entryRetryMs: number;
  /**
   * Не чаще одного `GET /state` за столько мс: после переподключения и дыр в версиях ресинки
   * просят подряд, а снимок — тяжёлый запрос. Лишние просьбы сливаются в один запрос.
   */
  resyncMinMs: number;
  /** 429 на снимок (`retry_after`): столько раз ждём и повторяем, потом снимок принесёт tick. */
  resyncRateLimitRetries: number;
  /** Потолок ожидания снимка (окно и `retry_after`), мс: огромный `Retry-After` или скачок часов не вешают ресинк. */
  resyncMaxWaitMs: number;
};

export const DEFAULT_REALTIME_CONFIG: RealtimeConfig = {
  tickMs: 5_000,
  tickFallbackMs: 1_000,
  offlineAfterMs: 20_000,
  acceptedResyncMs: 1_500,
  bufferLimit: 100,
  entryRetries: 5,
  entryRetryMs: 600,
  resyncMinMs: 1_000,
  resyncRateLimitRetries: 3,
  resyncMaxWaitMs: 30_000,
};
