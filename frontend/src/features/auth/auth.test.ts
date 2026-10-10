import { describe, expect, it } from "vitest";
import { signInAsGuest, setupMockBackend } from "@/test/msw";
import { csrfToken } from "@/shared/lib/cookies";
import { safeNext } from "./AuthForms";
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
      "//evil.example",
      "/\\evil.example",
      "javascript:alert(1)",
      null,
      "",
    ]) {
      expect(safeNext(bad)).toBe("/");
    }
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
