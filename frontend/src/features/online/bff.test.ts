import { describe, expect, it, vi } from "vitest";
import { fetchOnlinePage, type OnlinePage } from "./bff";

const ok: OnlinePage = { status: "ok", blocks: null };
const expired: OnlinePage = { status: "session_expired", blocks: null };

describe("fetchOnlinePage", () => {
  it("ok и auth_required — без refresh", async () => {
    const refresh = vi.fn();
    const get = vi.fn().mockResolvedValue({ status: "auth_required" });
    await expect(fetchOnlinePage(get, refresh)).resolves.toEqual({ status: "auth_required" });
    expect(refresh).not.toHaveBeenCalled();
    expect(get).toHaveBeenCalledOnce();
  });

  it("session_expired → refresh и один повтор", async () => {
    const get = vi.fn().mockResolvedValueOnce(expired).mockResolvedValueOnce(ok);
    const refresh = vi.fn().mockResolvedValue(true);
    await expect(fetchOnlinePage(get, refresh)).resolves.toEqual(ok);
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("refresh не удался — session_expired (экран входа), без повтора", async () => {
    const get = vi.fn().mockResolvedValue(expired);
    const refresh = vi.fn().mockResolvedValue(false);
    await expect(fetchOnlinePage(get, refresh)).resolves.toEqual(expired);
    expect(get).toHaveBeenCalledOnce();
  });
});
