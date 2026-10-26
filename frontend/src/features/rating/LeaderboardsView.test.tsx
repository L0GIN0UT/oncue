import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { server, setupMockBackend, signInAsUser } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { LeaderboardsView } from "./LeaderboardsView";

setupMockBackend();

const URL_PAGE = "*/api/web/v1/pages/leaderboards";
const U = (n: number) => `00000000-0000-4000-8000-0000000000${String(n).padStart(2, "0")}`;
const tier = (family: string, step: number | null) => ({
  family,
  key: step ? `${family}_${step}` : family,
  step,
  title_key: `rating.tier.${family}`,
});
const entry = (place: number) => ({
  place,
  user_id: U(place),
  nickname: `игрок${place}`,
  rating: 2000 - place * 10,
  tier: tier("platinum", 2),
  matches_played: 30 + place,
});
const ladders = [
  { key: "film", domain: "film", formats: ["classic", "lite"], season: null, tiers: [] },
  { key: "music", domain: "music", formats: ["classic"], season: null, tiers: [] },
];
const weekly = (ladder: string) => ({
  ladder,
  week_start: "2026-10-19",
  items: [{ place: 1, user_id: U(1), nickname: "игрок1", delta: 42 }],
  me: null,
});
const page = (ladder: string, items: unknown[], next: string | null) => ({
  blocks: {
    ladders: { status: "ok", data: ladders },
    leaderboard: { status: "ok", data: { ladder, items, next_cursor: next, me: null } },
    weekly: { status: "ok", data: weekly(ladder) },
  },
});

describe("LeaderboardsView — лидерборды", () => {
  it("таблица, топ недели, пагинация по курсору и переключение ladder-а", async () => {
    const seen: string[] = [];
    server.use(
      http.get(URL_PAGE, ({ request }) => {
        const q = new URL(request.url).searchParams;
        seen.push(`${q.get("ladder") ?? ""}|${q.get("cursor") ?? ""}`);
        if (q.get("ladder") === "music") return HttpResponse.json(page("music", [entry(1)], null));
        return q.get("cursor") === "c2"
          ? HttpResponse.json(page("film", [entry(3)], null))
          : HttpResponse.json(page("film", [entry(1), entry(2)], "c2"));
      }),
    );
    renderWithProviders(<LeaderboardsView />);
    const board = await screen.findByTestId("board");
    expect(within(board).getByTestId("board-row-1")).toHaveTextContent("игрок1");
    expect(within(board).getByTestId("board-row-1")).toHaveTextContent("Платина 2");
    expect(screen.getByRole("radio", { name: "Кино" })).toHaveAttribute("aria-checked", "true");
    // Гость: «Моё место» недоступно.
    expect(screen.queryByTestId("around-me")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Топ недели" }));
    expect(screen.getByTestId("weekly-film")).toHaveTextContent("+42");
    await userEvent.click(screen.getByRole("tab", { name: "Рейтинг" }));

    await userEvent.click(screen.getByTestId("board-more"));
    await waitFor(() => expect(screen.getByTestId("board-row-3")).toBeInTheDocument());
    expect(screen.queryByTestId("board-more")).not.toBeInTheDocument();
    expect(seen).toEqual(["|", "|c2"]);

    await userEvent.click(screen.getByRole("radio", { name: "Музыка" }));
    await waitFor(() =>
      expect(screen.getByRole("radio", { name: "Музыка" })).toHaveAttribute("aria-checked", "true"),
    );
    await userEvent.click(screen.getByRole("tab", { name: "Топ недели" }));
    expect(await screen.findByTestId("weekly-music")).toBeInTheDocument();
    expect(seen.at(-1)).toBe("music|");
  });

  it("404 ladder не найден — сообщение и возврат к началу", async () => {
    server.use(
      http.get(URL_PAGE, () =>
        HttpResponse.json(
          { type: "x", title: "x", status: 404, code: "bff.not_found" },
          { status: 404 },
        ),
      ),
    );
    renderWithProviders(<LeaderboardsView />);
    expect(await screen.findByTestId("board-error")).toHaveTextContent("Такого рейтинга нет");
  });

  it("блоки гаснут независимо: нет топа недели и списка ladder-ов — таблица на месте", async () => {
    server.use(
      http.get(URL_PAGE, () =>
        HttpResponse.json({
          blocks: {
            ladders: { status: "unavailable", data: null },
            leaderboard: {
              status: "ok",
              data: { ladder: "film", items: [entry(1)], next_cursor: null },
            },
            weekly: { status: "unavailable", data: null },
          },
        }),
      ),
    );
    renderWithProviders(<LeaderboardsView />);
    expect(await screen.findByTestId("board")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Топ недели" }));
    expect(screen.getByText(/Топ недели не загрузился/)).toBeInTheDocument();
    expect(screen.getByText(/Список разделов не загрузился/)).toBeInTheDocument();
  });

  const problem = (status: number, code: string) =>
    HttpResponse.json({ type: "x", title: "x", status, code }, { status });

  it("смена ladder-а: прошлая таблица и кнопки остаются под индикатором обновления", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.get(URL_PAGE, async ({ request }) => {
        const ladder = new URL(request.url).searchParams.get("ladder");
        if (ladder === "music") {
          await gate;
          return HttpResponse.json(page("music", [entry(7)], null));
        }
        return HttpResponse.json(page("film", [entry(1)], null));
      }),
    );
    renderWithProviders(<LeaderboardsView />);
    await screen.findByTestId("board-row-1");
    await userEvent.click(screen.getByRole("radio", { name: "Музыка" }));
    expect(await screen.findByTestId("board-updating")).toBeInTheDocument();
    // Пока грузится: старая таблица на месте, переключатель не пропал.
    expect(screen.getByTestId("board-row-1")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Кино" })).toBeInTheDocument();
    release();
    await screen.findByTestId("board-row-7");
    expect(screen.queryByTestId("board-updating")).not.toBeInTheDocument();
  });

  it("422 с курсором — устаревший курсор; кнопки ladder-ов не пропадают", async () => {
    server.use(
      http.get(URL_PAGE, ({ request }) =>
        new URL(request.url).searchParams.get("cursor")
          ? problem(422, "common.validation_failed")
          : HttpResponse.json(page("film", [entry(1)], "c2")),
      ),
    );
    renderWithProviders(<LeaderboardsView />);
    await userEvent.click(await screen.findByTestId("board-more"));
    expect(await screen.findByTestId("board-error")).toHaveTextContent("Список обновился");
    expect(screen.getByTestId("board-row-1")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Музыка" })).toBeInTheDocument();
  });

  it("422 без курсора — некорректный раздел, возврат к началу", async () => {
    server.use(
      http.get(URL_PAGE, ({ request }) =>
        new URL(request.url).searchParams.get("ladder") === "bad!"
          ? problem(422, "common.validation_failed")
          : HttpResponse.json(page("film", [entry(1)], null)),
      ),
    );
    renderWithProviders(<LeaderboardsView />);
    await screen.findByTestId("board-row-1");
    // Раздел выбирается только из списка; некорректный приходит снаружи — эмулируем ответом сервера.
    server.use(http.get(URL_PAGE, () => problem(422, "common.validation_failed")));
    await userEvent.click(screen.getByRole("radio", { name: "Музыка" }));
    expect(await screen.findByTestId("board-error")).toHaveTextContent("Некорректный раздел");
    expect(screen.getByRole("radio", { name: "Кино" })).toBeInTheDocument();
  });

  it("401 на «Моё место» — around сбрасывается, показано «Войдите»", async () => {
    signInAsUser();
    server.use(
      http.get("*/api/identity/v1/me", () => HttpResponse.json({ id: U(9) })),
      http.get(URL_PAGE, ({ request }) =>
        new URL(request.url).searchParams.get("around") === "me"
          ? problem(401, "auth.unauthenticated")
          : HttpResponse.json(page("film", [entry(1)], null)),
      ),
    );
    renderWithProviders(<LeaderboardsView />);
    await screen.findByTestId("board-row-1");
    await userEvent.click(await screen.findByTestId("around-me"));
    const alert = await screen.findByTestId("board-error");
    expect(alert).toHaveTextContent("Войдите");
    await userEvent.click(within(alert).getByRole("button"));
    expect(await screen.findByTestId("board-row-1")).toBeInTheDocument();
    expect(screen.getByTestId("around-me")).toHaveAttribute("aria-pressed", "false");
  });

  it("у таблицы есть подпись для скринридеров", async () => {
    server.use(http.get(URL_PAGE, () => HttpResponse.json(page("film", [entry(1)], null))));
    renderWithProviders(<LeaderboardsView />);
    const table = await screen.findByRole("table", { name: "Таблица лидеров · Кино" });
    // Заголовок колонок и одна строка игрока.
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  });
});
