import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { HomePage } from "@/shared/api/types";

// Восстановление сессии подменяем: тест проверяет сам факт и однократность, не сеть identity
// (refresh или гостевая — `recoverSession`, см. session.test.ts).
const recoverSession = vi.fn(async () => undefined);
vi.mock("@/features/auth/session", () => ({ recoverSession: () => recoverSession() }));

const card = {
  mode_key: "music.daily.classic",
  domain: "music",
  title_key: "modes.music.daily.classic.title",
  day: "2026-10-14",
  status: "not_started",
  max_score: 30,
  next_reset_at: "2026-10-14T21:00:00Z",
  session_id: null,
  score: null,
} as const;
const ok: HomePage = {
  blocks: { modes: { status: "ok", data: [] }, dailies: { status: "ok", data: [card] } },
};
const unavailable: HomePage = {
  blocks: { modes: { status: "ok", data: [] }, dailies: { status: "unavailable" } },
};

let next: HomePage = ok;
const get = vi.fn(async () => ({ data: next, error: undefined, response: new Response() }));
vi.mock("@/shared/api/client", () => ({
  api: () => ({ web: { GET: get } }),
  unwrap: (r: { data: unknown }) => r.data,
}));

describe("DailyBoard — восстановление сессии для блока дейликов", () => {
  beforeEach(() => {
    recoverSession.mockClear();
    get.mockClear();
    next = ok;
  });

  it("при недоступном блоке (нет сессии или access истёк) восстанавливает сессию и показывает карточки", async () => {
    const { DailyBoard } = await import("./DailyCards");
    renderWithProviders(<DailyBoard initial={unavailable} />);
    await waitFor(() => expect(recoverSession).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId("daily-music.daily.classic")).toBeInTheDocument();
  });

  it("если блок остаётся недоступным (сбой game), повторно не восстанавливает", async () => {
    next = unavailable;
    const { DailyBoard } = await import("./DailyCards");
    renderWithProviders(<DailyBoard initial={unavailable} />);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(recoverSession).toHaveBeenCalledTimes(1);
  });

  it("при доступном блоке сессию не трогает", async () => {
    const { DailyBoard } = await import("./DailyCards");
    renderWithProviders(<DailyBoard initial={ok} />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(recoverSession).not.toHaveBeenCalled();
    expect(screen.getByTestId("daily-music.daily.classic")).toBeInTheDocument();
  });
});
