import { describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError } from "@/shared/api/errors";
import { sendCommand } from "./commands";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("sendCommand", () => {
  it("тело — с op_id и expected_version, путь — команда комнаты", async () => {
    const fetch = vi.fn(async (_req: Request) => json(200, { version: 8, room: {} }));
    const out = await sendCommand(
      "K7MQ2XWP",
      "start",
      { expected_version: 7 },
      { fetch, opId: "op-12345678" },
    );
    expect(out).toEqual({ kind: "done", data: { version: 8, room: {} } });
    const req = fetch.mock.calls[0]![0];
    expect(new URL(req.url).pathname).toBe("/api/match/v1/rooms/K7MQ2XWP/start");
    expect(await req.json()).toEqual({ expected_version: 7, op_id: "op-12345678" });
  });

  it("op_id по умолчанию — uuid, повтор после обрыва — с тем же op_id", async () => {
    const bodies: unknown[] = [];
    const fetch = vi.fn(async (req: Request) => {
      bodies.push(await req.json());
      if (bodies.length === 1) throw new NetworkError();
      return json(200, { version: 3 });
    });
    await sendCommand(
      "K7MQ2XWP",
      "match/skip",
      { expected_version: 2, level: 1, round_index: 0 },
      {
        fetch,
        sleep: async () => {},
      },
    );
    expect(bodies).toHaveLength(2);
    expect(bodies[0]).toEqual(bodies[1]);
    expect((bodies[0] as { op_id: string }).op_id).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("202 — accepted с версией", async () => {
    const fetch = vi.fn(async () => json(202, { accepted: true, version: 11 }));
    await expect(sendCommand("K7MQ2XWP", "ready", { ready: true }, { fetch })).resolves.toEqual({
      kind: "accepted",
      version: 11,
    });
  });

  it("ошибка сервера — ApiError с кодом и details, без повтора", async () => {
    const fetch = vi.fn(async () =>
      json(422, { code: "match.chat_rejected", status: 422, details: { reason: "denylist" } }),
    );
    const err = await sendCommand("K7MQ2XWP", "match/chat", { text: "…" }, { fetch }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("match.chat_rejected");
    expect((err as ApiError).details).toEqual({ reason: "denylist" });
    expect(fetch).toHaveBeenCalledOnce();
  });

  it("сетевые повторы исчерпаны — NetworkError", async () => {
    const fetch = vi.fn(async () => {
      throw new NetworkError();
    });
    await expect(
      sendCommand("K7MQ2XWP", "leave", {}, { fetch, retries: 1, sleep: async () => {} }),
    ).rejects.toBeInstanceOf(NetworkError);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
