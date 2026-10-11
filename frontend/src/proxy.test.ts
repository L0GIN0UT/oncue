import { describe, expect, it, vi } from "vitest";
import { buildCsp, proxy } from "./proxy";
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
