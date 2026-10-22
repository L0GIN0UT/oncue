import { describe, expect, it } from "vitest";
import { signInAsGuest, setupMockBackend } from "@/test/msw";
import { csrfToken } from "@/shared/lib/cookies";
import {
  rememberNext,
  safeNext,
  takeRememberedNext,
  verifyEmailToken,
  withNext,
} from "./AuthForms";
import { parseConsents } from "./consents";
import { ensureSession, startGuestSession } from "./session";

setupMockBackend();

describe("сессия перед игрой", () => {
  it("без сессии выдаётся гостевая (cookie gv_csrf появляется)", async () => {
    expect(csrfToken()).toBeNull();
    await ensureSession();
    expect(csrfToken()).toBeTruthy();
  });

  it("при действующей сессии гостевая не запрашивается, 409 — не ошибка", async () => {
    signInAsGuest("a");
    await ensureSession();
    expect(csrfToken()).toBe("csrf-a");
    await expect(startGuestSession()).resolves.toBeUndefined();
  });
});

describe("safeNext", () => {
  it("пропускает только относительные пути своего сайта", () => {
    expect(safeNext("/play/music.daily.classic")).toBe("/play/music.daily.classic");
    for (const bad of [
      "https://evil.example",
      "https://evil.example/",
      "//evil.example",
      "/\\evil.example",
      "/\\evil.com",
      "/%09/evil.com",
      "/%0a/evil.com",
      "/%0d/evil.com",
      "/%5C/evil.com",
      "/\t/evil.com",
      "/\n/evil.com",
      "/%E0%A4%A",
      "javascript:alert(1)",
      null,
      "",
    ]) {
      expect(safeNext(bad, "https://oncue.test"), String(bad)).toBe("/");
    }
  });

  it("возвращает путь, query и fragment своего origin", () => {
    expect(safeNext("/results/s1?share=1#top", "https://oncue.test")).toBe(
      "/results/s1?share=1#top",
    );
    expect(safeNext("/a/../solo", "https://oncue.test")).toBe("/solo");
  });
});

describe("parseConsents", () => {
  it("разбирает формат GV_CONSENTS__REQUIRED и умолчание identity", () => {
    expect(parseConsents("terms:1, privacy:2")).toEqual([
      { kind: "terms", version: "1" },
      { kind: "privacy", version: "2" },
    ]);
    expect(parseConsents(undefined).map((c) => c.kind)).toEqual(["terms", "privacy", "age_18"]);
    expect(parseConsents("broken,:1,x:")).toEqual([]);
  });
});

describe("next регистрации", () => {
  it("ссылки входа и регистрации несут только безопасный next", () => {
    expect(withNext("/register", "/room/K7MQ2XWP")).toBe("/register?next=%2Froom%2FK7MQ2XWP");
    expect(withNext("/login", "//evil.example")).toBe("/login");
    expect(withNext("/login", null)).toBe("/login");
  });

  it("next запоминается до подтверждения e-mail, читается один раз и перепроверяется", () => {
    rememberNext("/online");
    expect(takeRememberedNext()).toBe("/online");
    expect(takeRememberedNext()).toBeNull();
    localStorage.setItem("gv_auth_next", "https://evil.example/");
    expect(takeRememberedNext()).toBeNull();
  });
});

describe("подтверждение e-mail при лимите gateway", () => {
  const noSleep = async () => {};

  it("429 — повтор с паузой, затем успех (токен не израсходован)", async () => {
    const statuses = [429, 429, 204];
    const waits: number[] = [];
    const result = await verifyEmailToken(
      async () => ({ status: statuses.shift()!, retryAfterS: 2 }),
      async (ms) => void waits.push(ms),
    );
    expect(result).toBe("ok");
    expect(waits).toEqual([2000, 2000]);
  });

  it("лимит не отпустил — rate_limited, а не «ссылка недействительна»", async () => {
    await expect(verifyEmailToken(async () => ({ status: 429 }), noSleep)).resolves.toBe(
      "rate_limited",
    );
  });

  it("4xx кроме 429 — недействительная ссылка без повторов", async () => {
    let calls = 0;
    const result = await verifyEmailToken(async () => ((calls += 1), { status: 400 }), noSleep);
    expect(result).toBe("failed");
    expect(calls).toBe(1);
  });
});
