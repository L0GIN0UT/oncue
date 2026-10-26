import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import type { HomePage } from "@/shared/api/types";
import { TodayGame } from "./TodayGame";

// Старт сессии проверяется в StartGame.test.tsx; здесь важно, какой режим выбран по домену.
vi.mock("@/features/game/GameScreen", () => ({
  GameScreen: ({ sessionId }: { sessionId: string }) => <p>игра {sessionId}</p>,
}));
vi.mock("@/features/game/StartGame", () => ({
  StartGame: ({ modeKey, onSession }: { modeKey: string; onSession?: (id: string) => void }) => (
    <button onClick={() => onSession?.("s1")}>старт {modeKey}</button>
  ),
}));
const recoverSession = vi.fn(async () => undefined);
vi.mock("@/features/auth/session", () => ({ recoverSession: () => recoverSession() }));
const get = vi.fn();
vi.mock("@/shared/api/client", () => ({
  api: () => ({ web: { GET: get } }),
  unwrap: (r: { data: unknown }) => r.data,
}));

const card = (domain: "music" | "film") => ({
  mode_key: `${domain}.daily.classic`,
  domain,
  title_key: "t",
  day: "2026-10-26",
  status: "not_started",
  max_score: 30,
  next_reset_at: "2026-10-27T00:00:00Z",
  session_id: null,
  score: null,
  presentation: "score",
});
const home = (dailies: HomePage["blocks"]["dailies"]): HomePage => ({
  blocks: { modes: { status: "ok", data: [] }, dailies, weekly: { status: "ok", data: [] } },
});

describe("TodayGame — главная это сегодняшняя игра", () => {
  beforeEach(() => {
    recoverSession.mockClear();
    get.mockReset();
  });

  it.each([
    ["music", "music.daily.classic"],
    ["film", "film.daily.classic"],
  ] as const)("домен %s стартует свой дейлик", (domain, modeKey) => {
    const initial = home({ status: "ok", data: [card("music"), card("film")] } as never);
    renderWithProviders(<TodayGame initial={initial} domain={domain} />);
    expect(screen.getByText(`старт ${modeKey}`)).toBeInTheDocument();
  });

  it("готовая сессия показывается на месте, без перехода", async () => {
    const initial = home({ status: "ok", data: [card("music")] } as never);
    renderWithProviders(<TodayGame initial={initial} domain="music" />);
    await userEvent.click(screen.getByRole("button", { name: /старт music/ }));
    expect(screen.getByText("игра s1")).toBeInTheDocument();
  });

  it("блок недоступен: ждём восстановления сессии, ошибку не показываем", async () => {
    get.mockResolvedValue({ data: home({ status: "unavailable" }) });
    renderWithProviders(<TodayGame initial={home({ status: "unavailable" })} domain="music" />);
    expect(screen.getByText("Готовим раунд…")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    await vi.waitFor(() => expect(recoverSession).toHaveBeenCalledTimes(1));
  });

  it("дейлика нужного домена нет в готовом блоке — сообщение о недоступности", () => {
    const initial = home({ status: "ok", data: [card("music")] } as never);
    renderWithProviders(<TodayGame initial={initial} domain="film" />);
    expect(screen.getByRole("alert")).toHaveTextContent("Загадки дня сейчас не загрузились");
  });
});
