/**
 * Связь с комнатой (D1 плана этапа 4, ADR 0035): канал Centrifugo + HTTP-tick + ресинк.
 *
 * - Снимок `GET /v1/rooms/{code}/state` — при старте, при дыре в версиях, после подписки
 *   без recovery и если после 202 дельта не пришла вовремя. Параллельные ресинки сливаются.
 * - Сообщения канала проходят {@link VersionGate}: по порядку, без повторов.
 * - `POST /v1/rooms/{code}/tick` — heartbeat присутствия и фолбэк снимка: раз в `tickMs` при
 *   живом WebSocket и раз в `tickFallbackMs` без него (HTTP-stream или канала нет).
 * - Нет ни канала, ни успешного tick дольше `offlineAfterMs` — статус `offline` (баннер).
 * - Часы сервера — по `server_now` ответов и сообщений ({@link ServerClock}).
 *
 * 403/404 на tick или снимке — комната закрыта или игрок больше не участник: связь
 * останавливается и сообщает `onFatal`.
 */
import { isApiError } from "@/shared/api/errors";
import type { RoomState, TickResult } from "@/shared/api/types";
import { ServerClock } from "./clock";
import { DEFAULT_REALTIME_CONFIG, type RealtimeConfig } from "./config";
import { parseRoomMessage, type RoomMessage } from "./message";
import type { RoomTransport, TransportKind } from "./transport";
import { VersionGate } from "./version-gate";

export type LinkStatus = {
  /** Транспорт активной подписки; `null` — канала нет, состояние держит tick. */
  transport: TransportKind;
  /** Связи нет дольше `offlineAfterMs`. */
  offline: boolean;
};

export type RoomLinkDeps = {
  transport: RoomTransport | null;
  fetchState: () => Promise<RoomState>;
  tick: (knownVersion: number | null) => Promise<TickResult>;
  clock?: ServerClock;
  config?: Partial<RealtimeConfig>;
  localNow?: () => number;
  /** Полный снимок (заменяет состояние целиком). */
  onState: (state: RoomState) => void;
  /** Сообщение канала по порядку версий. */
  onMessage: (message: RoomMessage) => void;
  onStatus?: (status: LinkStatus) => void;
  onFatal?: (error: unknown) => void;
};

export class RoomLink {
  readonly clock: ServerClock;
  private readonly gate: VersionGate;
  private readonly config: RealtimeConfig;
  private readonly localNow: () => number;
  private status: LinkStatus = { transport: null, offline: false };
  private tickTimer: ReturnType<typeof setTimeout> | null = null;
  private offlineTimer: ReturnType<typeof setTimeout> | null = null;
  private acceptedTimer: ReturnType<typeof setTimeout> | null = null;
  private resyncing: Promise<void> | null = null;
  private resyncAgain = false;
  private stopped = true;

  constructor(private readonly deps: RoomLinkDeps) {
    this.config = { ...DEFAULT_REALTIME_CONFIG, ...deps.config };
    this.localNow = deps.localNow ?? (() => Date.now());
    this.clock = deps.clock ?? new ServerClock(this.localNow);
    this.gate = new VersionGate(this.config.bufferLimit);
  }

  /** Версия, которую видит клиент (`expected_version` команд). */
  get version(): number | null {
    return this.gate.version;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.armOffline();
    void this.resync();
    this.deps.transport?.start({
      onPublication: (data) => this.receive(data),
      onSubscribed: ({ transport, recovered }) => {
        this.setStatus({ transport });
        this.contact();
        // Без recovery сообщения между отпиской и подпиской потеряны — догоняем снимком.
        if (!recovered) void this.resync();
        this.scheduleTick();
      },
      onLost: () => {
        if (this.status.transport === null) return;
        this.setStatus({ transport: null });
        this.scheduleTick();
      },
    });
    this.scheduleTick();
  }

  stop(): void {
    this.stopped = true;
    for (const t of [this.tickTimer, this.offlineTimer, this.acceptedTimer]) if (t) clearTimeout(t);
    this.tickTimer = this.offlineTimer = this.acceptedTimer = null;
    this.deps.transport?.stop();
  }

  /** Принять сообщение канала (сырые данные публикации). */
  receive(data: unknown): void {
    if (this.stopped) return;
    const message = parseRoomMessage(data);
    if (!message) return;
    this.contact();
    this.clock.observe(message.server_now);
    const { apply, resync } = this.gate.offer(message);
    for (const m of apply) this.deps.onMessage(m);
    // Снимок уже в пути — он и закроет дыру; остаток догонит следующий tick.
    if (resync && !this.resyncing) void this.resync();
  }

  /**
   * Полный снимок комнаты, пришедший не через `RoomLink` (ответ команды лобби): применяется,
   * только если он не старее того, что уже показано.
   */
  acceptSnapshot(state: RoomState): void {
    this.applyState(state);
  }

  /**
   * Команда принята (202 `CommandAccepted`): владелец не ответил вовремя. Если за
   * `acceptedResyncMs` версия не ушла дальше `version`, берём снимок.
   */
  awaitAccepted(version: number): void {
    if (this.acceptedTimer) clearTimeout(this.acceptedTimer);
    this.acceptedTimer = setTimeout(() => {
      this.acceptedTimer = null;
      if ((this.gate.version ?? 0) <= version) void this.resync();
    }, this.config.acceptedResyncMs);
  }

  /** Снимок по REST (single-flight; запрос во время ресинка — ещё один после него). */
  resync(): Promise<void> {
    if (this.resyncing) {
      this.resyncAgain = true;
      return this.resyncing;
    }
    const run = async () => {
      do {
        this.resyncAgain = false;
        const sentAt = this.localNow();
        try {
          const state = await this.deps.fetchState();
          if (this.stopped) return;
          this.contact();
          this.clock.observe(state.room.server_now, sentAt);
          this.applyState(state);
        } catch (e) {
          if (this.stopped) return;
          if (this.isFatal(e)) return this.fail(e);
          // Сеть или 5xx: снимок принесёт следующий tick.
        }
      } while (this.resyncAgain && !this.stopped);
    };
    this.resyncing = run().finally(() => {
      this.resyncing = null;
    });
    return this.resyncing;
  }

  private applyState(state: RoomState): void {
    const pending = this.gate.reset(state.room.version);
    if (pending === null) return;
    this.deps.onState(state);
    for (const m of pending) this.deps.onMessage(m);
  }

  private async tick(): Promise<void> {
    const sentAt = this.localNow();
    try {
      const result = await this.deps.tick(this.gate.version);
      if (this.stopped) return;
      this.contact();
      this.clock.observe(result.server_now, sentAt);
      if (result.state) this.applyState(result.state);
      else if (this.gate.version !== null && result.version > this.gate.version) {
        // Сервер ушёл вперёд, а снимка в ответе нет — догоняем явно.
        void this.resync();
      }
    } catch (e) {
      if (this.stopped) return;
      if (this.isFatal(e)) this.fail(e);
    }
  }

  private scheduleTick(): void {
    if (this.stopped) return;
    if (this.tickTimer) clearTimeout(this.tickTimer);
    const interval =
      this.status.transport === "websocket" ? this.config.tickMs : this.config.tickFallbackMs;
    this.tickTimer = setTimeout(async () => {
      this.tickTimer = null;
      await this.tick();
      this.scheduleTick();
    }, interval);
  }

  private contact(): void {
    if (this.status.offline) this.setStatus({ offline: false });
    this.armOffline();
  }

  private armOffline(): void {
    if (this.offlineTimer) clearTimeout(this.offlineTimer);
    this.offlineTimer = setTimeout(() => {
      this.offlineTimer = null;
      if (!this.stopped) this.setStatus({ offline: true });
    }, this.config.offlineAfterMs);
  }

  private setStatus(patch: Partial<LinkStatus>): void {
    const next = { ...this.status, ...patch };
    if (next.transport === this.status.transport && next.offline === this.status.offline) return;
    this.status = next;
    this.deps.onStatus?.(next);
  }

  private isFatal(e: unknown): boolean {
    return isApiError(e) && (e.status === 403 || e.status === 404 || e.status === 401);
  }

  private fail(e: unknown): void {
    this.stop();
    this.deps.onFatal?.(e);
  }
}
