import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { setBannedHandler } from "./banned";
import { REFRESH_PATH, refreshSession, type RefreshDeps } from "./refresh";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";

setupMockBackend();

afterEach(() => setBannedHandler());

const problem = (status: number, code: string) =>
  new Response(JSON.stringify({ status, code, title: code }), {
    status,
    headers: { "Content-Type": "application/problem+json" },
  });

describe("бан аккаунта при открытой сессии", () => {
  it("access истёк, refresh вернул 403 auth.account_banned — переход на экран блокировки", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    signInAsUser();
    world.scenario.social = { sessionBanned: true };
    const result = await api().web.GET("/v1/session");
    expect(result.response.status).toBe(401);
    expect(onBanned).toHaveBeenCalledTimes(1);
  });

  it("любой запрос с 403 auth.account_banned (не вход) — тоже переход", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    const original = globalThis.fetch;
    globalThis.fetch = async () => problem(403, "auth.account_banned");
    try {
      await api().social.GET("/v1/me/sanctions");
    } finally {
      globalThis.fetch = original;
    }
    expect(onBanned).toHaveBeenCalledTimes(1);
  });

  it("вход с auth.account_banned экрана не открывает: ошибку показывает форма", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    world.users.push({
      id: "u1",
      nickname: "bob",
      email: "bob@example.com",
      password: "correct-horse",
      verified: true,
    });
    world.scenario.social = { accountBanned: true };
    const result = await api().identity.POST("/v1/auth/login", {
      body: { login: "bob", password: "correct-horse" },
    });
    expect(result.response.status).toBe(403);
    expect(onBanned).not.toHaveBeenCalled();
  });

  it("другие 403 (csrf) не считаются баном", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    const original = globalThis.fetch;
    globalThis.fetch = async () => problem(403, "auth.csrf_failed");
    try {
      await api().social.GET("/v1/me/sanctions");
    } finally {
      globalThis.fetch = original;
    }
    expect(onBanned).not.toHaveBeenCalled();
  });
});

describe("refreshSession и бан", () => {
  const deps = (res: Response): RefreshDeps => ({
    fetch: (async () => res) as unknown as typeof fetch,
    locks: null,
    readCsrf: () => "c",
  });

  it("403 auth.account_banned — refresh не удался и сообщено о бане", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    expect(await refreshSession("c", deps(problem(403, "auth.account_banned")))).toBe(false);
    expect(onBanned).toHaveBeenCalledTimes(1);
  });

  it("обычный отказ refresh (401) бан не объявляет", async () => {
    const onBanned = vi.fn();
    setBannedHandler(onBanned);
    expect(await refreshSession("c", deps(problem(401, "auth.refresh_invalid")))).toBe(false);
    expect(onBanned).not.toHaveBeenCalled();
    expect(REFRESH_PATH).toContain("refresh");
  });
});
