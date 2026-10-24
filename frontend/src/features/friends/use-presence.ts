"use client";
/**
 * Присутствие: пока вкладка видима, раз в `PRESENCE_HEARTBEAT_MS` шлём social
 * `POST /v1/me/presence` (ключ в Redis живёт дольше периода). Скрытая вкладка молчит — друзья
 * видят «офлайн»; при возврате сигнал уходит сразу. Ошибки сигнала не важны: следующий придёт
 * через период.
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

/** Запустить цикл сигналов; возвращает остановку. Вынесено из хука ради тестов. */
export function startPresence(
  send: () => Promise<void>,
  intervalMs: number,
  doc: VisibilityDoc = document,
): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;

  const beat = () => {
    void send().catch(() => undefined);
  };
  const start = () => {
    if (timer !== null) return;
    beat();
    timer = setInterval(beat, intervalMs);
  };
  const stop = () => {
    if (timer === null) return;
    clearInterval(timer);
    timer = null;
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
