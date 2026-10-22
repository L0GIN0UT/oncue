/**
 * Канал комнаты в e2e на моках — без Centrifugo (ADR 0035 тестируется unit-тестами
 * `shared/realtime`). Скрипт до загрузки приложения ставит `window.__GV_E2E_REALTIME__`:
 * транспорт с интерфейсом `RoomTransport`, который опрашивает ленту мока
 * `GET /api/match/__e2e/rooms/{code}/feed?after=N` (150 мс). Лента 503 — «канал потерян»,
 * клиент переходит на частый tick; дыры в версиях мок создаёт сценарием `dropNextMessage`.
 */
import type { BrowserContext, Page } from "@playwright/test";

function fakeRealtime() {
  type Handlers = {
    onPublication(data: unknown): void;
    onSubscribed(info: { transport: "websocket"; recovered: boolean }): void;
    onLost(): void;
  };
  (window as unknown as { __GV_E2E_REALTIME__: unknown }).__GV_E2E_REALTIME__ = (code: string) => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let after = 0;
    let subscribed = false;
    return {
      start(h: Handlers) {
        const poll = async () => {
          if (stopped) return;
          try {
            const res = await fetch(`/api/match/__e2e/rooms/${code}/feed?after=${after}`, {
              credentials: "same-origin",
              cache: "no-store",
            });
            if (!res.ok) throw new Error(String(res.status));
            const { messages, last } = (await res.json()) as {
              messages: { version: number }[];
              last: number;
            };
            if (stopped) return;
            if (!subscribed) {
              subscribed = true;
              h.onSubscribed({ transport: "websocket", recovered: false });
            }
            for (const m of messages) h.onPublication(m);
            after = Math.max(after, last);
          } catch {
            if (subscribed) {
              subscribed = false;
              after = 0;
              h.onLost();
            }
          }
          timer = setTimeout(poll, 150);
        };
        void poll();
      },
      stop() {
        stopped = true;
        if (timer) clearTimeout(timer);
      },
    };
  };
}

export async function installFakeRealtime(target: Page | BrowserContext): Promise<void> {
  await target.addInitScript(fakeRealtime);
}
