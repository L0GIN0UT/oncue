/**
 * Чтение публичных экранов bff на сервере Next (SSR, D2). Без пользовательского токена:
 * личные блоки догружает клиент. Внутренний адрес — `GV_BFF_INTERNAL_URL`.
 */
import "server-only";
import createClient from "openapi-fetch";
import type { paths as BffPaths } from "./schema/bff_web";
import type { Domain, HomePage, ShellPage, SoloPage } from "./types";

const TIMEOUT_MS = 2000;

function bff() {
  const baseUrl = process.env.GV_BFF_INTERNAL_URL ?? "http://bff-web:8000";
  return createClient<BffPaths>({ baseUrl });
}

/** `null` — bff недоступен: страница рендерится с заглушками, клиент повторит запрос. */
async function safe<T>(fn: (signal: AbortSignal) => Promise<{ data?: T }>): Promise<T | null> {
  try {
    const { data } = await fn(AbortSignal.timeout(TIMEOUT_MS));
    return data ?? null;
  } catch {
    return null;
  }
}

export function fetchHomePage(): Promise<HomePage | null> {
  return safe((signal) => bff().GET("/v1/pages/home", { signal, cache: "no-store" }));
}

export function fetchSoloPage(domain?: Domain): Promise<SoloPage | null> {
  return safe((signal) =>
    bff().GET("/v1/pages/solo", {
      params: { query: domain ? { domain } : {} },
      signal,
      cache: "no-store",
    }),
  );
}

/** Оболочка (тема, колокольчик): без токена — публичный ответ; `null` — bff недоступен (тема default). */
export function fetchShellPage(): Promise<ShellPage | null> {
  return safe((signal) => bff().GET("/v1/pages/shell", { signal, cache: "no-store" }));
}
