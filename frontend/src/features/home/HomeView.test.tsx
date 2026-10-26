import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@/test/render";
import type { HomePage } from "@/shared/api/types";
import { HomeView } from "./HomeView";
import { levelShare } from "./ProgressTile";

let kind: "user" | "guest" | "anonymous" = "user";

vi.mock("@/features/auth/session", () => ({
  recoverSession: async () => undefined,
  useViewer: () => ({ kind, pending: false }),
}));
vi.mock("@/features/online/bff", () => ({
  useOnlinePage: () => ({
    data: {
      status: "ok",
      blocks: {
        open_rooms: { status: "ok", data: { items: [{}, {}, {}] } },
        ratings: {
          status: "ok",
          data: [
            {
              ladder: "music",
              rating: 1480,
              calibrating: false,
              tier: { family: "silver", key: "silver_2", step: 2, title_key: "rating.tier.silver" },
            },
            { ladder: "film", rating: null, calibrating: true, tier: null },
          ],
        },
      },
    },
  }),
}));
vi.mock("@/features/collections/api", () => ({
  collectionKeys: { showcase: () => ["showcase"] },
  fetchShowcase: async () => ({
    blocks: {
      popular: {
        status: "ok",
        data: {
          items: [{ collection_id: "c1", title: "Брит-рок 90-х", domain: "music" }],
        },
      },
    },
  }),
}));

const card = (domain: "music" | "film", status: string, extra = {}) => ({
  mode_key: `${domain}.daily.classic`,
  domain,
  title_key: "t",
  day: "2026-10-26",
  status,
  max_score: 30,
  next_reset_at: "2026-10-27T00:00:00Z",
  session_id: null,
  score: null,
  presentation: "score",
  ...extra,
});

function page(over: Partial<HomePage["blocks"]> = {}): HomePage {
  return {
    blocks: {
      modes: {
        status: "ok",
        data: [{ key: "music.random.emoji", domain: "music", title_key: "x", rounds: 5 }],
      },
      dailies: {
        status: "ok",
        data: [
          card("music", "not_started"),
          card("film", "finished", { score: 24, session_id: "s9" }),
        ],
      },
      weekly: { status: "ok", data: [] },
      progress: {
        status: "ok",
        data: {
          level: 14,
          xp_in_level: 82,
          xp_to_next: 18,
          best_streak: 12,
          quests_open: 2,
          can_spin: false,
        },
      },
      event: null,
      ...over,
    },
  } as unknown as HomePage;
}

beforeEach(() => {
  kind = "user";
});

describe("HomeView", () => {
  it("дейлики ведут на /play/<mode_key>, сыгранный — на итоги", () => {
    renderWithProviders(<HomeView initial={page()} />);
    expect(screen.getByTestId("home-daily-music")).toHaveAttribute(
      "href",
      "/play/music.daily.classic",
    );
    expect(screen.getByTestId("home-daily-film")).toHaveAttribute("href", "/results/s9");
    expect(screen.getByTestId("home-daily-music")).toHaveTextContent("не сыгран");
    expect(screen.getByTestId("home-daily-film")).toHaveTextContent("сыгран · 24 из 30");
  });

  it("прогресс: уровень, серия, два ранга (калибровка у кино)", async () => {
    renderWithProviders(<HomeView initial={page()} />);
    const progress = screen.getByTestId("home-progress");
    expect(progress).toHaveTextContent("Уровень 14");
    expect(progress).toHaveTextContent("серия 12");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "82");
    expect(screen.getByTestId("home-rank-music")).toHaveTextContent(/1\s480/);
    expect(screen.getByTestId("home-rank-film")).toHaveTextContent("калибровка");
  });

  it("лента дейлика — пустое состояние с действием «Найти друзей»", () => {
    renderWithProviders(<HomeView initial={page()} />);
    expect(screen.getByTestId("home-feed-empty")).toBeInTheDocument();
    expect(screen.getByTestId("home-feed-action")).toHaveAttribute("href", "/friends");
  });

  it("гость: прогресс зовёт войти, лента ведёт на вход", () => {
    kind = "guest";
    renderWithProviders(<HomeView initial={page({ progress: null })} />);
    expect(screen.getByTestId("home-progress")).toHaveAttribute("href", "/login");
    expect(screen.getByTestId("home-feed-action")).toHaveAttribute("href", "/login");
    expect(screen.queryByTestId("home-online-rooms")).toBeNull();
  });

  it("нижний ряд: Соло, Онлайн (лобби), Сборники (популярные); события нет — плитки нет", async () => {
    renderWithProviders(<HomeView initial={page()} />);
    expect(screen.getByTestId("home-solo")).toHaveAttribute("href", "/solo");
    expect(screen.getByTestId("home-online-rooms")).toHaveTextContent("3");
    expect(await screen.findByText("Брит-рок 90-х")).toBeInTheDocument();
    expect(screen.queryByTestId("home-event")).toBeNull();
  });

  it("событие есть — плитка с прогрессом ведёт в дневник", () => {
    renderWithProviders(
      <HomeView
        initial={page({
          event: {
            key: "winter",
            title: "Дневник снегопада",
            ends_at: "2026-12-31T00:00:00Z",
            found: 7,
            total: 12,
          },
        })}
      />,
    );
    expect(screen.getByTestId("home-event")).toHaveAttribute("href", "/events/winter");
    expect(screen.getByTestId("home-event-progress")).toHaveTextContent("Найдено 7 из 12");
  });

  it("блок дейликов недоступен — плитки без ссылок, не падает", () => {
    renderWithProviders(
      <HomeView initial={page({ dailies: { status: "unavailable", data: null } })} />,
    );
    expect(screen.getByTestId("home-daily-music")).not.toHaveAttribute("href");
    expect(screen.getByTestId("home-daily-music")).toHaveTextContent("сейчас недоступен");
  });
});

describe("levelShare", () => {
  it("доля опыта в уровне; высший уровень — полная полоса", () => {
    expect(levelShare({ xp_in_level: 82, xp_to_next: 18 })).toBe(82);
    expect(levelShare({ xp_in_level: 0, xp_to_next: 100 })).toBe(0);
    expect(levelShare({ xp_in_level: 500, xp_to_next: null })).toBe(100);
    expect(levelShare({ xp_in_level: 0, xp_to_next: 0 })).toBe(0);
  });
});
