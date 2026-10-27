import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsGuest } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { GuessOut, RoundOut } from "@/shared/api/types";
import { AnswerBox } from "./AnswerBox";
import { RoundTabs, tabState } from "./RoundTabs";

setupMockBackend();

const guess = (kind: GuessOut["kind"], result: GuessOut["result"]): GuessOut => ({
  kind,
  result,
  display: "x",
});
const round = (index: number, over: Partial<RoundOut> = {}): RoundOut => ({
  index,
  level: 1,
  levels_count: 6,
  status: "pending",
  guesses: [],
  ...over,
});

describe("RoundTabs", () => {
  it("цвет вкладки — по исходу раунда, текущая янтарная, будущие пустые", () => {
    const rounds = [
      round(0, { status: "finished", points: 70, guesses: [guess("item", "exact")] }),
      round(1, { status: "finished", points: 25, guesses: [guess("item", "partial")] }),
      round(2, { status: "finished", points: 0, guesses: [guess("text", "miss")] }),
      round(3, { status: "finished", points: 0, guesses: [guess("skip", null)] }),
      round(4, { status: "active" }),
      round(5),
    ];
    renderWithProviders(<RoundTabs rounds={rounds} current={4} />);
    const tabs = screen.getByTestId("round-tabs").querySelectorAll("li");
    expect([...tabs].map((t) => t.getAttribute("data-state"))).toEqual([
      "exact",
      "partial",
      "miss",
      "skip",
      "current",
      "upcoming",
    ]);
    expect(screen.getByLabelText("Раунд 1: угадал")).toBeInTheDocument();
    expect(screen.getByLabelText("Раунд 5: текущий")).toHaveAttribute("aria-current", "step");
  });

  it("раунд без попыток и с отменой не засчитан как мимо", () => {
    expect(tabState(round(0, { status: "void" }), 0)).toBe("skip");
  });
});

describe("AnswerBox: последняя попытка", () => {
  it("вместо «Пропустить» красная «Сдаться», а с текстом — «Ответить»", async () => {
    signInAsGuest();
    const user = userEvent.setup();
    const onGiveUp = vi.fn();
    renderWithProviders(
      <AnswerBox
        domain="music"
        disabled={false}
        onAnswer={vi.fn()}
        giveUp={{ label: "Сдаться", onGiveUp, testId: "give-up" }}
      />,
    );
    await user.click(screen.getByTestId("give-up"));
    expect(onGiveUp).toHaveBeenCalledTimes(1);
    await user.type(screen.getByRole("combobox"), "что-то");
    expect(screen.queryByTestId("give-up")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ответить" })).toBeInTheDocument();
  });
});
