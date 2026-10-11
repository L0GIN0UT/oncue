/**
 * fetch для браузерных вызовов API: cookie, CSRF на мутациях, `no-store`, refresh на 401.
 *
 * Используется как `fetch` клиентов openapi-fetch. Сетевая ошибка превращается в
 * {@link NetworkError}, чтобы вызывающий мог повторить ход с тем же `Idempotency-Key`.
 */
import { CSRF_HEADER, csrfToken } from "../lib/cookies";
import { NetworkError } from "./errors";
import { refreshSession } from "./refresh";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
/**
 * Когда 401 стоит попробовать вылечить refresh-ом.
 *
 * `auth.token_expired` — всегда. `auth.unauthenticated` (запрос ушёл без access-токена) — только
 * если в браузере есть признак сессии (`gv_csrf`): cookie access могла исчезнуть раньше
 * refresh-сессии, и без refresh клиент ушёл бы в гостевую сессию, а она ответила бы 409.
 */
function shouldRefresh(code: string | null, csrfAtRequest: string | null): boolean {
  if (code === "auth.token_expired") return true;
  return code === "auth.unauthenticated" && csrfAtRequest !== null;
}

function prepare(request: Request): Request {
  const headers = new Headers(request.headers);
  if (!SAFE_METHODS.has(request.method)) {
    const csrf = csrfToken();
    if (csrf) headers.set(CSRF_HEADER, csrf);
  }
  return new Request(request, { headers, credentials: "same-origin", cache: "no-store" });
}

async function send(request: Request): Promise<Response> {
  try {
    return await globalThis.fetch(prepare(request));
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new NetworkError(e);
  }
}

async function problemCode(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.clone().json();
    return body && typeof body === "object" && "code" in body ? String(body.code) : null;
  } catch {
    return null;
  }
}

export async function apiFetch(request: Request): Promise<Response> {
  const retry = request.clone();
  const csrfAtRequest = csrfToken();
  const res = await send(request);
  if (res.status !== 401 || request.url.includes("/api/identity/v1/auth/")) return res;
  if (!shouldRefresh(await problemCode(res), csrfAtRequest)) return res;
  const ok = await refreshSession(csrfAtRequest);
  return ok ? send(retry) : res;
}
