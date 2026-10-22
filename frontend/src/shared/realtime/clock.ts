/**
 * Поправка часов клиента по `server_now` (ADR 0035): таймер фазы считается от
 * `phase_ends_at` сервера, а не от локальных часов, которые могут уходить на минуты.
 *
 * Смещение берётся по середине запроса (половина RTT), если она известна; для сообщений
 * канала — по моменту получения. Резкие скачки сглаживаются: новое смещение принимается
 * сразу, только если оно отличается больше чем на `jumpMs` (смена часов, сон ноутбука).
 */
export class ServerClock {
  private offsetMs: number | null = null;

  constructor(
    private readonly localNow: () => number = () => Date.now(),
    private readonly jumpMs = 1_000,
  ) {}

  /**
   * Учесть отметку сервера.
   *
   * @param serverNowIso `server_now` из ответа или сообщения канала.
   * @param sentAt локальное время отправки запроса (для tick/state), иначе — момент получения.
   */
  observe(serverNowIso: string | null | undefined, sentAt?: number): void {
    if (!serverNowIso) return;
    const server = Date.parse(serverNowIso);
    if (Number.isNaN(server)) return;
    const received = this.localNow();
    const local = sentAt === undefined ? received : (sentAt + received) / 2;
    const sample = server - local;
    if (this.offsetMs === null || Math.abs(sample - this.offsetMs) > this.jumpMs) {
      this.offsetMs = sample;
    } else {
      this.offsetMs = Math.round(this.offsetMs * 0.8 + sample * 0.2);
    }
  }

  /** Смещение «сервер − клиент», мс (0, пока отметок не было). */
  offset(): number {
    return this.offsetMs ?? 0;
  }

  /** Текущее время сервера по оценке клиента, мс эпохи. */
  now(): number {
    return this.localNow() + this.offset();
  }

  /** Сколько осталось до `endsAtIso` по часам сервера (не меньше 0); `null` — без таймера. */
  remainingMs(endsAtIso: string | null | undefined): number | null {
    if (!endsAtIso) return null;
    const end = Date.parse(endsAtIso);
    if (Number.isNaN(end)) return null;
    return Math.max(0, end - this.now());
  }
}
