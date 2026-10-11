/**
 * Proxy Next (бывший middleware): CSP с nonce на каждый ответ-страницу (план §7, §9 п. 7).
 *
 * Остальные заголовки безопасности ставит gateway (infra/nginx/snippets/web-headers.conf).
 * В e2e на моках (`GV_E2E=1` + `GV_DEV_API_ORIGIN`) `/api/*` и `/media/*` переписываются на
 * мок-бэкенд; в проде эти пути обслуживает nginx (см. shared/lib/dev-api.ts).
 */
import { NextResponse, type NextRequest } from "next/server";
import { devApiOrigin } from "@/shared/lib/dev-api";

/** Политика для страниц. `style-src-attr 'unsafe-inline'` — только атрибуты `style` (React SSR). */
export function buildCsp(nonce: string, dev: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    `style-src-elem 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "media-src 'self' blob:",
    "font-src 'self'",
    `connect-src 'self'${dev ? " ws:" : ""}`,
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

function devRewrite(request: NextRequest): NextResponse | null {
  const origin = devApiOrigin();
  const { pathname, search } = request.nextUrl;
  if (!origin || !(pathname.startsWith("/api/") || pathname.startsWith("/media/"))) return null;
  return NextResponse.rewrite(new URL(pathname + search, origin));
}

export function proxy(request: NextRequest): NextResponse {
  const rewritten = devRewrite(request);
  if (rewritten) return rewritten;

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp(nonce, process.env.NODE_ENV === "development");
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|icon.svg|healthz).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
