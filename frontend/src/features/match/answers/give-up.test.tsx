import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import type { MyRoundView } from "@/shared/api/types";
import { ClassicMatchAnswer } from "./ClassicMatchAnswer";
import { PickMatchAnswer } from "./PickMatchAnswer";
import type { MatchAnswerProps } from "./types";

const me: MyRoundView = {
  attempts_left: 3,
  guesses: [],
  round_index: 0,
  status: "thinking",
  options: [
    { option_id: "a", title: "Первый" },
    { option_id: "b", title: "Второй" },
  ],
};

function props(over: Partial<MatchAnswerProps> = {}): MatchAnswerProps {
  return {
    domain: "music",
    me,
    disabled: false,
    canSkip: true,
    lastLevel: false,
    onGuess: vi.fn(),
    onSkip: vi.fn(),
    ...over,
  };
}

describe("комната, classic: «Сдаться» на последнем уровне", () => {
  it("на последнем уровне вместо «Пропустить» красная «Сдаться», она шлёт skip", async () => {
    const onSkip = vi.fn();
    renderWithProviders(
      <ClassicMatchAnswer {...props({ canSkip: false, lastLevel: true, onSkip })} />,
    );
    expect(screen.queryByTestId("match-skip")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("match-give-up"));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("на обычном уровне — «Пропустить», «Сдаться» нет", () => {
    renderWithProviders(<ClassicMatchAnswer {...props()} />);
    expect(screen.getByTestId("match-skip")).toBeInTheDocument();
    expect(screen.queryByTestId("match-give-up")).not.toBeInTheDocument();
  });

  it("при введённом тексте на последнем уровне кнопка — «Ответить»", async () => {
    renderWithProviders(<ClassicMatchAnswer {...props({ canSkip: false, lastLevel: true })} />);
    await userEvent.type(screen.getByTestId("answer-input"), "что-то");
    expect(screen.getByTestId("answer-submit")).toBeEnabled();
    expect(screen.queryByTestId("match-give-up")).not.toBeInTheDocument();
  });

  it("недоступна, пока ход невозможен", () => {
    renderWithProviders(
      <ClassicMatchAnswer {...props({ canSkip: false, lastLevel: true, disabled: true })} />,
    );
    expect(screen.getByTestId("match-give-up")).toBeDisabled();
  });
});

describe("комната, pick: «Сдаться» на последнем уровне", () => {
  it("на последнем уровне есть «Сдаться», она шлёт skip", async () => {
    const onSkip = vi.fn();
    renderWithProviders(<PickMatchAnswer {...props({ lastLevel: true, onSkip })} />);
    await userEvent.click(screen.getByTestId("match-give-up"));
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("на обычном уровне кнопки нет", () => {
    renderWithProviders(<PickMatchAnswer {...props()} />);
    expect(screen.queryByTestId("match-give-up")).not.toBeInTheDocument();
  });

  it("недоступна, пока ход невозможен", () => {
    renderWithProviders(<PickMatchAnswer {...props({ lastLevel: true, disabled: true })} />);
    expect(screen.getByTestId("match-give-up")).toBeDisabled();
  });
});
