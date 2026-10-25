"use client";
/**
 * Присутствие: пока вкладка видима, раз в `PRESENCE_HEARTBEAT_MS` шлём social
 * `POST /v1/me/presence` (ключ в Redis живёт дольше периода). Скрытая вкладка молчит — друзья
 * видят «офлайн»; при возврате сигнал уходит сразу. Ошибки сигнала не важны: следующий придёт
 * через период.
 *
 * Сигнал уходит из одной вкладки на браузер: цикл идёт только под эксклюзивной Web Lock
 * `PRESENCE_LOCK`. Вкладка берёт замок, когда видима, и отпускает, когда скрыта, — сигнал шлёт
 * та, что на виду; остальные ждут в очереди замка. Без Web Locks (старые браузеры) каждая
 * видимая вкладка шлёт сама, как раньше.
 */
import { useEffect } from "react";
import { useViewer } from "@/features/auth/session";
import { sendPresence } from "./api";
import { PRESENCE_HEARTBEAT_MS } from "./config";

type VisibilityDoc = {
  readonly visibilityState: DocumentVisibilityState;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
};

/** Имя замка присутствия: общее для всех вкладок одного origin. */
export const PRESENCE_LOCK = "gv-presence";

type LockManagerLike = {
  request(
    name: string,
    options: { signal: AbortSignal },
    callback: () => Promise<void>,
  ): Promise<unknown>;
};

/** Запустить цикл сигналов; возвращает остановку. Вынесено из хука ради тестов. */
export function startPresence(
  send: () => Promise<void>,
  intervalMs: number,
  doc: VisibilityDoc = document,
  locks: LockManagerLike | null = typeof navigator === "undefined"
    ? null
    : ((navigator as { locks?: LockManagerLike }).locks ?? null),
): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  let abort: AbortController | null = null;
  let release: (() => void) | null = null;

  const beat = () => {
    void send().catch(() => undefined);
  };
  const run = () => {
    if (timer !== null) return;
    beat();
    timer = setInterval(beat, intervalMs);
  };
  const halt = () => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
  };
  const start = () => {
    if (locks === null) return run();
    if (abort !== null) return;
    const controller = new AbortController();
    abort = controller;
    // Замок держим, пока вкладка видима и жива; отпускает stop().
    locks
      .request(PRESENCE_LOCK, { signal: controller.signal }, () => {
        // stop() мог случиться между выдачей замка и коллбэком: тогда замок отпускаем сразу.
        if (controller.signal.aborted || abort !== controller) return Promise.resolve();
        run();
        return new Promise<void>((resolve) => {
          release = resolve;
        });
      })
      .catch(() => undefined);
  };
  const stop = () => {
    halt();
    abort?.abort();
    abort = null;
    release?.();
    release = null;
  };
  const onVisibility = () => (doc.visibilityState === "visible" ? start() : stop());

  doc.addEventListener("visibilitychange", onVisibility);
  onVisibility();
  return () => {
    doc.removeEventListener("visibilitychange", onVisibility);
    stop();
  };
}

/** Монтируется один раз в шапке; только для пользователя с аккаунтом. */
export function PresenceHeartbeat() {
  const viewer = useViewer();
  const user = viewer.kind === "user";
  useEffect(() => {
    if (!user) return;
    return startPresence(sendPresence, PRESENCE_HEARTBEAT_MS);
  }, [user]);
  return null;
}
