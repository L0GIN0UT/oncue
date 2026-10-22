/**
 * Контроль `version` сообщений канала (ADR 0035): применяются только по порядку без дыр.
 *
 * - `version ≤ текущей` — повтор или устаревшее (recovery Centrifugo, гонка с REST) — пропуск;
 * - `version = текущей + 1` — применить и дотянуть из буфера следующие подряд;
 * - больше — дыра: сообщение в буфер, вызывающему — `resync` (`GET /v1/rooms/{code}/state`).
 *
 * После снимка (`reset`) из буфера применяются сообщения новее снимка, если они идут подряд.
 */
import type { RoomMessage } from "./message";

export type GateDecision = { apply: RoomMessage[]; resync: boolean };

export class VersionGate {
  private current: number | null = null;
  private readonly buffer = new Map<number, RoomMessage>();

  constructor(private readonly bufferLimit = 100) {}

  /** Версия последнего применённого состояния; `null` — снимка ещё не было. */
  get version(): number | null {
    return this.current;
  }

  /** Принять сообщение канала. */
  offer(message: RoomMessage): GateDecision {
    if (this.current === null) {
      // Снимок ещё грузится: держим сообщение, решит `reset`.
      this.hold(message);
      return { apply: [], resync: false };
    }
    if (message.version <= this.current) return { apply: [], resync: false };
    if (message.version === this.current + 1) {
      this.current = message.version;
      return { apply: [message, ...this.drain()], resync: false };
    }
    this.hold(message);
    return { apply: [], resync: true };
  }

  /**
   * Принять полный снимок версии `version` (state, tick, ответ команды комнаты).
   *
   * @returns `null` — снимок старее уже применённого (медленный ответ), его не применять;
   *   иначе — сообщения из буфера, которые надо применить поверх снимка.
   */
  reset(version: number): RoomMessage[] | null {
    if (this.current !== null && version < this.current) return null;
    this.current = version;
    for (const v of [...this.buffer.keys()]) if (v <= version) this.buffer.delete(v);
    return this.drain();
  }

  /** Есть ли дыра: в буфере ждут сообщения, которые нельзя применить без ресинка. */
  get hasGap(): boolean {
    return this.buffer.size > 0;
  }

  private hold(message: RoomMessage): void {
    this.buffer.set(message.version, message);
    if (this.buffer.size > this.bufferLimit) {
      // Держим самые новые: старые всё равно перекроет снимок.
      const oldest = Math.min(...this.buffer.keys());
      this.buffer.delete(oldest);
    }
  }

  private drain(): RoomMessage[] {
    const out: RoomMessage[] = [];
    while (this.current !== null && this.buffer.has(this.current + 1)) {
      const next = this.buffer.get(this.current + 1)!;
      this.buffer.delete(next.version);
      this.current = next.version;
      out.push(next);
    }
    return out;
  }
}
