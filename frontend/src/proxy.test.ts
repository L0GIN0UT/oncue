import { describe, expect, it, vi } from "vitest";
import { buildCsp, proxy, wsSourceOf } from "./proxy";
import { DevApiConfigError, devApiOrigin } from "./shared/lib/dev-api";

describe("CSP страниц", () => {
  it("скрипты только с nonce, медиа и картинки — свои и blob:, без встраивания во фреймы", () => {
    const csp = buildCsp("abc", false);
    expect(csp).toContain("script-src 'self' 'nonce-abc' 'strict-dynamic'");
    expect(csp).not.toContain("unsafe-eval");
    expect(csp).toContain("media-src 'self' blob:");
    expect(csp).toContain("img-src 'self' blob: data:");
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).not.toMatch(/script-src[^;]*unsafe-inline/);
  });

  it("unsafe-eval — только в разработке", () => {
    expect(buildCsp("n", true)).toContain("'unsafe-eval'");
  });

  it("connect-src явно разрешает wss:// своего хоста (realtime комнат)", () => {
    const csp = buildCsp("n", false, "OnCue.example:8443");
    expect(csp).toContain("connect-src 'self' wss://oncue.example:8443;");
    expect(csp).not.toMatch(/connect-src[^;]* ws:/);
  });

  it("хост с подозрительными символами в CSP не попадает", () => {
    for (const host of ["evil.com; script-src *", "a b", "*.example", "x/y", "", null]) {
      expect(wsSourceOf(host)).toBeNull();
      expect(buildCsp("n", false, host)).toContain("connect-src 'self';");
    }
  });

  it("proxy берёт хост запроса", async () => {
    const { NextRequest } = await import("next/server");
    const res = proxy(new NextRequest("https://play.example/room/ABCDEFGH"));
    expect(res.headers.get("Content-Security-Policy")).toContain(
      "connect-src 'self' wss://play.example",
    );
  });
});

describe("обход gateway (GV_DEV_API_ORIGIN)", () => {
  it("включается только с GV_E2E=1", () => {
    expect(devApiOrigin({ GV_DEV_API_ORIGIN: "http://127.0.0.1:3101", GV_E2E: "1" })).toBe(
      "http://127.0.0.1:3101",
    );
    expect(devApiOrigin({})).toBeNull();
    expect(devApiOrigin({ GV_E2E: "1" })).toBeNull();
  });

  it("GV_DEV_API_ORIGIN без GV_E2E=1 — ошибка запуска, а не тихий обход", () => {
    for (const flag of [undefined, "0", "true"]) {
      expect(() =>
        devApiOrigin({ GV_DEV_API_ORIGIN: "http://evil.example", GV_E2E: flag }),
      ).toThrow(DevApiConfigError);
    }
  });

  it("proxy не переписывает /api/* без GV_E2E=1", async () => {
    const { NextRequest } = await import("next/server");
    vi.stubEnv("GV_DEV_API_ORIGIN", "http://127.0.0.1:3101");
    vi.stubEnv("GV_E2E", "");
    try {
      expect(() => proxy(new NextRequest("http://localhost/api/game/v1/modes"))).toThrow(
        DevApiConfigError,
      );
      vi.stubEnv("GV_E2E", "1");
      const res = proxy(new NextRequest("http://localhost/api/game/v1/modes"));
      expect(res.headers.get("x-middleware-rewrite")).toBe(
        "http://127.0.0.1:3101/api/game/v1/modes",
      );
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
