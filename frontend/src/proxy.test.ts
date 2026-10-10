import { describe, expect, it } from "vitest";
import { buildCsp } from "./proxy";

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
