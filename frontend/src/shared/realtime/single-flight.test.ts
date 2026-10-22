import { describe, expect, it, vi } from "vitest";
import { singleFlight } from "./single-flight";
import { centrifugeEndpoints } from "./transport";

describe("singleFlight", () => {
  it("параллельные вызовы с одним ключом — один запрос; разные ключи — разные", async () => {
    let release!: (v: string) => void;
    const run = vi.fn(
      (code: string) => new Promise<string>((resolve) => (release = (v) => resolve(v + code))),
    );
    const get = singleFlight((code: string) => code, run);
    const a = get("A");
    const b = get("A");
    expect(run).toHaveBeenCalledTimes(1);
    release("t-");
    await expect(Promise.all([a, b])).resolves.toEqual(["t-A", "t-A"]);
    void get("B");
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("после завершения (и ошибки) следующий вызов идёт заново", async () => {
    const run = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce("ok");
    const get = singleFlight(() => "k", run);
    await expect(get()).rejects.toThrow("boom");
    await expect(get()).resolves.toBe("ok");
    expect(run).toHaveBeenCalledTimes(2);
  });
});

describe("адреса Centrifugo", () => {
  it("WebSocket — wss:// своего хоста, фолбэк — HTTP-stream и эмуляция", () => {
    expect(centrifugeEndpoints("https://oncue.example")).toEqual({
      transports: [
        { transport: "websocket", endpoint: "wss://oncue.example/connection/websocket" },
        { transport: "http_stream", endpoint: "https://oncue.example/connection/http_stream" },
      ],
      emulationEndpoint: "https://oncue.example/connection/emulation",
    });
  });
});
