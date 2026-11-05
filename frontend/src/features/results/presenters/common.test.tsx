import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { signInAsUser, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { ResultSummary } from "@/shared/api/types";
import { ResultRows } from "./common";

setupMockBackend();

const round = (index: number, over: Record<string, unknown> = {}) => ({
  index,
  outcome: "exact" as const,
  level: 1,
  points: 5,
  media_item_id: `00000000-0000-4000-a000-00000000000${index}`,
  ...over,
});

const summary = (rounds: unknown[]) =>
  ({ score: 0, max_score: 25, rounds }) as unknown as ResultSummary;

describe("ResultRows", () => {
  it("«Сдаться» без уровня: «вы сдались», а не множественное без подлежащего", () => {
    signInAsUser();
    renderWithProviders(
      <ResultRows
        summary={summary([round(0, { outcome: "gave_up", level: null, points: 0 })])}
        items={new Map()}
        modeTitle="m"
        domain="music"
        levels={[]}
      />,
    );
    expect(screen.getByTestId("result-row")).toHaveTextContent("вы сдались");
  });

  it("«Сохранить» у нескольких строк — компактно: без текста на каждой, общая подпись один раз", () => {
    signInAsUser();
    renderWithProviders(
      <ResultRows
        summary={summary([round(0), round(1), round(2), round(3), round(4)])}
        items={new Map()}
        modeTitle="m"
        domain="music"
        levels={[]}
      />,
    );
    const buttons = screen.getAllByTestId("save-button");
    expect(buttons).toHaveLength(5);
    for (const b of buttons) expect(b).toHaveAttribute("data-compact", "true");
    expect(screen.getAllByTestId("results-save-hint")).toHaveLength(1);
  });

  it("одна строка — обычная кнопка «Сохранить» без общей подписи", () => {
    signInAsUser();
    renderWithProviders(
      <ResultRows
        summary={summary([round(0)])}
        items={new Map()}
        modeTitle="m"
        domain="music"
        levels={[]}
      />,
    );
    expect(screen.getByTestId("save-button")).not.toHaveAttribute("data-compact");
    expect(screen.queryByText("Сохранить в медиатеку")).not.toBeInTheDocument();
  });
});
