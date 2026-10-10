/**
 * Мок-бэкенд для unit-тестов: те же обработчики, что у Playwright (e2e/mocks), поверх MSW node.
 *
 * В jsdom `fetch` — node-реализация без cookie-хранилища браузера, поэтому обёртка добавляет
 * `document.cookie` к запросам своего origin и записывает `Set-Cookie` ответов обратно
 * (атрибут HttpOnly в тестах игнорируется).
 */
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll } from "vitest";
import { createHandlers } from "../../e2e/mocks/handlers";
import { MockWorld } from "../../e2e/mocks/world";

export const world = new MockWorld();
export const server = setupServer(...createHandlers(world));

function clearCookies(): void {
  for (const c of document.cookie.split(";")) {
    const name = c.split("=")[0]?.trim();
    if (name) document.cookie = `${name}=; Max-Age=0; Path=/`;
  }
}

function withCookieJar(inner: typeof fetch): typeof fetch {
  return async (input, init) => {
    const request = new Request(input instanceof URL ? input.href : input, init);
    const sameOrigin = new URL(request.url).origin === globalThis.location.origin;
    if (sameOrigin && document.cookie) request.headers.set("cookie", document.cookie);
    const response = await inner(request);
    if (sameOrigin) {
      for (const raw of response.headers.getSetCookie()) {
        const [pair = ""] = raw.split(";");
        document.cookie = `${pair}; Path=/${/max-age=0/i.test(raw) ? "; Max-Age=0" : ""}`;
      }
    }
    return response;
  };
}

/** Подключить мок-сервер к файлу тестов. */
export function setupMockBackend(): void {
  let original: typeof fetch;
  beforeAll(() => {
    server.listen({ onUnhandledFrame: "error" });
    original = globalThis.fetch;
    globalThis.fetch = withCookieJar(original);
  });
  afterEach(() => {
    server.resetHandlers();
    world.reset();
    clearCookies();
  });
  afterAll(() => {
    globalThis.fetch = original;
    server.close();
  });
}

/** Войти в мок как гость: cookie `gv_access` и `gv_csrf`. */
export function signInAsGuest(id = "test"): string {
  const subject = `guest.${id}`;
  document.cookie = `gv_access=${subject}; Path=/`;
  document.cookie = `gv_csrf=csrf-${id}; Path=/`;
  return subject;
}
