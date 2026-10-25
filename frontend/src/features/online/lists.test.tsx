import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
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
  it("у участника с can_report есть кнопка жалобы с понятным именем", () => {
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
    const button = screen.getByTestId("history-report-trinity");
    expect(button).toHaveAccessibleName("Пожаловаться на игрока: trinity");
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
