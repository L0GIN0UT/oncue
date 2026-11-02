import { describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { conflictOf, isLivePhase, leaveRoom } from "./active-room";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("conflictOf", () => {
  it("409 active_room_conflict с details.code → комната и фаза", () => {
    const e = new ApiError(409, {
      code: "match.active_room_conflict",
      details: { code: "K7MQ2XWP", phase: "level_open" },
    });
    expect(conflictOf(e)).toEqual({ code: "K7MQ2XWP", phase: "level_open" });
  });

  it("старый сервер без phase — фаза неизвестна", () => {
    const e = new ApiError(409, {
      code: "match.active_room_conflict",
      details: { code: "K7MQ2XWP" },
    });
    expect(conflictOf(e)).toEqual({ code: "K7MQ2XWP", phase: null });
  });

  it("конфликт очереди (details.queue без code) и чужие ошибки — не конфликт комнаты", () => {
    expect(
      conflictOf(
        new ApiError(409, {
          code: "match.active_room_conflict",
          details: { queue: { ladder: "music" } },
        }),
      ),
    ).toBeNull();
    expect(conflictOf(new ApiError(409, { code: "match.phase_mismatch" }))).toBeNull();
    expect(conflictOf(new NetworkError())).toBeNull();
  });
});

describe("isLivePhase", () => {
  it("идущий матч — выход засчитается как сдача; лобби, итог и закрытая — нет", () => {
    for (const p of ["countdown", "level_open", "level_closed", "reveal", "paused"] as const)
      expect(isLivePhase(p)).toBe(true);
    for (const p of ["lobby", "summary", "closed", null] as const)
      expect(isLivePhase(p)).toBe(false);
  });
});

describe("leaveRoom", () => {
  it("шлёт leave с op_id", async () => {
    const fetch = vi.fn(async (_req: Request) => json(200, { version: 4, room: {} }));
    await leaveRoom("K7MQ2XWP", { fetch });
    const req = fetch.mock.calls[0]![0];
    expect(new URL(req.url).pathname).toBe("/api/match/v1/rooms/K7MQ2XWP/leave");
    expect(await req.json()).toEqual({ op_id: expect.any(String) });
  });

  it.each([403, 404])("%i (уже не участник / комнаты нет) — выход состоялся", async (status) => {
    const fetch = vi.fn(async () => json(status, { code: "match.room_not_found" }));
    await expect(leaveRoom("K7MQ2XWP", { fetch })).resolves.toBeUndefined();
  });

  it("сервер недоступен — ошибка наружу, состояние не очищаем", async () => {
    const fetch = vi.fn(async () => json(503, { code: "match.rooms_unavailable" }));
    await expect(leaveRoom("K7MQ2XWP", { fetch })).rejects.toBeInstanceOf(ApiError);
  });
});
