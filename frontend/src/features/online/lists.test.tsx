import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { MyMatchCard } from "@/shared/api/types";
import { MyMatchList } from "./lists";

setupMockBackend();

const BASE: MyMatchCard = {
  match_id: "0192a3b4-c5d6-7e8f-9a0b-000000000d01",
  mode: {
    key: "film.duel.frame",
    version: 1,
    domain: "film",
    kind: "duel",
    title_key: "modes.film.duel.frame.title",
    clue_type: "frame_progressive",
    answer_format: "classic",
    ranked: false,
  },
  finished_at: "2026-10-21T19:00:00Z",
  finish: "completed",
  players: 2,
  place: 1,
  score: 36,
  outcome: "win",
  my_finish: "completed",
};

describe("MyMatchList: игроки матча для жалобы (ADR 0069)", () => {
  it("у участника с can_report есть пункт жалобы с понятным именем", async () => {
    renderWithProviders(
      <MyMatchList
        matches={[
          {
            ...BASE,
            participants: [{ user_id: "u-2", nickname: "trinity", can_report: true }],
          },
        ]}
      />,
    );
    await userEvent.click(screen.getByTestId(`history-report-menu-${BASE.match_id}`));
    expect(await screen.findByTestId("history-report-trinity")).toHaveAccessibleName(
      "Пожаловаться на игрока: trinity",
    );
  });

  it("у строки матча одна иконка-меню на всех соперников, а не «Пожаловаться» у каждого", async () => {
    renderWithProviders(
      <MyMatchList
        matches={[
          {
            ...BASE,
            participants: [
              { user_id: "u-2", nickname: "trinity", can_report: true },
              { user_id: "u-3", nickname: "morpheus", can_report: true },
            ],
          },
        ]}
      />,
    );
    // До открытия меню кнопок жалобы на игроков нет, есть одна кнопка меню без видимого текста.
    expect(screen.queryByTestId("history-report-trinity")).toBeNull();
    const menu = screen.getByTestId(`history-report-menu-${BASE.match_id}`);
    expect(screen.getAllByRole("button", { name: /Пожаловаться/ })).toEqual([menu]);
    expect(menu).toHaveAttribute("aria-haspopup", "menu");
    expect(menu).toHaveTextContent("");
    await userEvent.click(menu);
    const items = within(await screen.findByRole("menu")).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent)).toEqual([
      "Пожаловаться на игрока: trinity",
      "Пожаловаться на игрока: morpheus",
    ]);
    // Пункт открывает диалог жалобы на выбранного игрока.
    await userEvent.click(items[1]!);
    expect(await screen.findByTestId("report-dialog")).toBeInTheDocument();
  });

  it("без participants (старый ответ, сбой источника) кнопок нет", () => {
    renderWithProviders(<MyMatchList matches={[BASE, { ...BASE, participants: [] }]} />);
    expect(screen.queryByTestId(/^history-report-/)).toBeNull();
  });

  it("can_report = false — ника нет в списке жалоб", () => {
    renderWithProviders(
      <MyMatchList
        matches={[
          { ...BASE, participants: [{ user_id: "u-2", nickname: "trinity", can_report: false }] },
        ]}
      />,
    );
    expect(screen.queryByTestId("history-report-trinity")).toBeNull();
  });
});
