/**
 * Охранный тест (само воспроизведение — AvatarBlock.test.tsx): у multipart-запроса (загрузка аватарки) в окружении vitest (jsdom + undici)
 * копия заголовков через `new Headers(request.headers)` теряла все, кроме `content-type` с
 * границей, — пропадал `Idempotency-Key`. `apiFetch` копирует заголовки перебором.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "./http";

afterEach(() => vi.unstubAllGlobals());

describe("apiFetch: заголовки multipart-запроса", () => {
  it("Idempotency-Key и CSRF доходят вместе с content-type границы", async () => {
    document.cookie = "gv_csrf=csrf-1; Path=/";
    const seen: Request[] = [];
    vi.stubGlobal("fetch", async (r: Request) => {
      seen.push(r);
      return new Response(null, { status: 204 });
    });
    const form = new FormData();
    form.append(
      "file",
      new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "a.png", { type: "image/png" }),
    );
    const request = new Request("http://localhost/api/progression/v1/me/avatar", {
      method: "POST",
      body: form,
      headers: { "Idempotency-Key": "key-12345678" },
    });

    await apiFetch(request);

    expect(seen).toHaveLength(1);
    expect(seen[0]!.headers.get("idempotency-key")).toBe("key-12345678");
    expect(seen[0]!.headers.get("x-csrf-token")).toBe("csrf-1");
    expect(seen[0]!.headers.get("content-type")).toMatch(/^multipart\/form-data; boundary=/);
    document.cookie = "gv_csrf=; Max-Age=0; Path=/";
  });
});
