import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { HomePage } from "@/shared/api/types";

// Гостевую сессию и cookie подменяем: тест проверяет сам факт бутстрапа, не сеть identity.
const ensureSession = vi.fn(async () => undefined);
let csrf = "";
vi.mock("@/features/auth/session", () => ({ ensureSession: () => ensureSession() }));
vi.mock("@/shared/lib/cookies", () => ({ csrfToken: () => csrf }));

// Перезапрос `/v1/pages/home` после сессии возвращает блок с карточками.
const ok: HomePage = {
  blocks: {
    modes: { status: "ok", data: [] },
    dailies: {
      status: "ok",
      data: [
        {
          mode_key: "music.daily.classic",
          domain: "music",
          title_key: "modes.music.daily.classic.title",
          day: "2026-10-14",
          status: "not_started",
          max_score: 30,
          next_reset_at: "2026-10-14T21:00:00Z",
          session_id: null,
          score: null,
        },
      ],
    },
  },
};
const get = vi.fn(async () => ({ data: ok, error: undefined, response: new Response() }));
vi.mock("@/shared/api/client", () => ({
  api: () => ({ web: { GET: get } }),
  unwrap: (r: { data: unknown }) => r.data,
}));

const anonymous: HomePage = {
  blocks: { modes: { status: "ok", data: [] }, dailies: { status: "unavailable" } },
};

describe("DailyBoard — бутстрап гостевой сессии", () => {
  beforeEach(() => {
    ensureSession.mockClear();
    get.mockClear();
    csrf = "";
  });

  it("анониму заводит гостевую сессию и показывает карточки после перезапроса", async () => {
    const { DailyBoard } = await import("./DailyCards");
    renderWithProviders(<DailyBoard initial={anonymous} />);
    await waitFor(() => expect(ensureSession).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("daily-music.daily.classic")).toBeInTheDocument();
  });

  it("при живой сессии (есть cookie) гостевую не заводит", async () => {
    csrf = "csrf-token";
    const { DailyBoard } = await import("./DailyCards");
    renderWithProviders(<DailyBoard initial={anonymous} />);
    // Ждём обычный перезапрос главной (react-query обновляет initialData на монтировании) —
    // значит, эффекты отработали, — и убеждаемся, что гостевую сессию не заводили.
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(ensureSession).not.toHaveBeenCalled();
  });
});
