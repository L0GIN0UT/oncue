import { describe, expect, it } from "vitest";
import { MODES } from "../../../e2e/mocks/world";
import { api, unwrap } from "@/shared/api/client";
import { ApiError } from "@/shared/api/errors";
import { csrfToken } from "@/shared/lib/cookies";
import { dropAccessCookie, setupMockBackend, signInAsGuest, world } from "@/test/msw";
import { startGuestSession, withSession } from "./session";

setupMockBackend();

const snapshot = (id: string) =>
  api().game.GET("/v1/sessions/{session_id}", { params: { path: { session_id: id } } });

describe("исчезла cookie access, refresh-сессия жива", () => {
  it("401 auth.unauthenticated при наличии gv_csrf — refresh и повтор запроса", async () => {
    const s = world.startSession(signInAsGuest("a"), MODES[0]!, null);
    dropAccessCookie();
    const result = await snapshot(s.id);
    expect(result.response.status).toBe(200);
    expect(world.refreshCalls).toBe(1);
    expect(csrfToken()).not.toBe("csrf-a"); // refresh перевыдал cookie
  });

  it("без gv_csrf (сессии не было) refresh не вызывается", async () => {
    const result = await snapshot("0192a000-0000-7000-8000-000000000001");
    expect(result.response.status).toBe(401);
    expect(world.refreshCalls).toBe(0);
  });

  it("withSession продолжает ту же сессию, а не заводит гостевую", async () => {
    const subject = signInAsGuest("b");
    const s = world.startSession(subject, MODES[0]!, null);
    dropAccessCookie();
    const got = await withSession(async () => unwrap(await snapshot(s.id)));
    expect(got.id).toBe(s.id);
  });
});

describe("startGuestSession и 409", () => {
  it("409 при живой refresh-сессии — успех после refresh", async () => {
    signInAsGuest("c");
    dropAccessCookie();
    await expect(startGuestSession()).resolves.toBeUndefined();
    expect(world.refreshCalls).toBe(1);
  });

  it("409 без успешного refresh — ошибка, а не «успех»", async () => {
    signInAsGuest("d");
    world.revokedRefresh.add("guest.d");
    const err = await startGuestSession().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("auth.already_authenticated");
  });
});
