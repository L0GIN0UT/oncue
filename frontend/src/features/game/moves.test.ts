import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server, signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { MODES } from "../../../e2e/mocks/world";
import { sendMove } from "./moves";

setupMockBackend();

const noSleep = () => Promise.resolve();

describe("sendMove", () => {
  it("повторяет ход после сетевой ошибки с тем же Idempotency-Key и CSRF", async () => {
    const subject = signInAsGuest();
    const s = world.startSession(subject, MODES[0]!, null);
    const seen: { key: string | null; csrf: string | null }[] = [];
    let failed = false;
    server.events.on("request:start", ({ request }) => {
      if (request.url.endsWith("/skips"))
        seen.push({
          key: request.headers.get("idempotency-key"),
          csrf: request.headers.get("x-csrf-token"),
        });
    });
    server.use(
      http.post("*/api/game/v1/sessions/:id/skips", () => {
        if (failed) return undefined;
        failed = true;
        return HttpResponse.error();
      }),
    );
    const result = await sendMove(
      { sessionId: s.id, roundIndex: 0, expectedVersion: 0 },
      { kind: "skip" },
      { sleep: noSleep },
    );
    server.events.removeAllListeners();
    expect(result.round.level).toBe(2);
    expect(seen).toHaveLength(2);
    expect(seen[0]!.key).toBeTruthy();
    expect(seen[1]!.key).toBe(seen[0]!.key);
    expect(seen[0]!.csrf).toBe("csrf-test");
  });

  it("повтор с тем же ключом не делает второй ход (сервер отдаёт сохранённый ответ)", async () => {
    const subject = signInAsGuest();
    const s = world.startSession(subject, MODES[0]!, null);
    const target = { sessionId: s.id, roundIndex: 0, expectedVersion: 0 };
    const a = await sendMove(target, { kind: "skip" }, { key: "fixed-key-123" });
    const b = await sendMove(target, { kind: "skip" }, { key: "fixed-key-123" });
    expect(b).toEqual(a);
    expect(world.sessions.get(s.id)!.version).toBe(1);
  });

  it("исчерпав повторы, бросает NetworkError", async () => {
    signInAsGuest();
    server.use(http.post("*/api/game/v1/sessions/:id/guesses", () => HttpResponse.error()));
    await expect(
      sendMove(
        { sessionId: "x", roundIndex: 0, expectedVersion: 0 },
        { kind: "guess", answer: { text: "a" } },
        { sleep: noSleep },
      ),
    ).rejects.toBeInstanceOf(NetworkError);
  });

  it("ответ сервера не повторяется: 409 game.desync с кодом и снимком", async () => {
    const subject = signInAsGuest();
    const s = world.startSession(subject, MODES[0]!, null);
    const err = await sendMove(
      { sessionId: s.id, roundIndex: 0, expectedVersion: 7 },
      { kind: "give_up" },
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("game.desync");
    expect((err as ApiError).details).toHaveProperty("session.version", 0);
  });
});
