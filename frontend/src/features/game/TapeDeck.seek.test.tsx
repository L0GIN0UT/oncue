import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import type { MyRoundView } from "@/shared/api/types";
import { PickMatchAnswer } from "@/features/match/answers/PickMatchAnswer";
import { TapeDeck, tapeSeekTarget } from "./TapeDeck";

// Лента 16 с на 320 px: 20 px на секунду. Уровни 0,5 / 1 / 2 / 4 / 8 / 16 с.
const RECT = { left: 0, width: 320, right: 320 } as DOMRect;
const levels = [0.5, 1, 2, 4, 8, 16].map((seconds, i) => ({
  level: i + 1,
  hint: { seconds },
})) as never;
const weights = [0.5, 0.5, 1, 2, 4, 8];

function setup(opts: { level?: number; time?: number } = {}) {
  const seek = vi.fn<(seconds: number) => void>();
  const level = opts.level ?? 3; // открыто 2 с
  const open = [0.5, 1, 2, 4, 8, 16][level - 1]!;
  renderWithProviders(
    <TapeDeck
      weights={weights}
      levels={levels}
      level={level}
      progress={null}
      seek={{ time: opts.time ?? 0, open, seek }}
    />,
  );
  const bar = screen.getByRole("slider");
  vi.spyOn(bar, "getBoundingClientRect").mockReturnValue(RECT);
  return { bar, seek };
}

describe("TapeDeck: перемотка по ленте", () => {
  it("клик внутри открытой части перематывает на соответствующую секунду", () => {
    const { bar, seek } = setup();
    fireEvent.pointerDown(bar, { clientX: 20 }); // 1 с
    expect(seek).toHaveBeenLastCalledWith(1);
  });

  it("клик правее открытой границы прижимается к ней", () => {
    const { bar, seek } = setup();
    fireEvent.pointerDown(bar, { clientX: 300 }); // 15 с при открытых 2
    expect(seek).toHaveBeenLastCalledWith(2);
  });

  it("перетаскивание перематывает до отпускания", () => {
    const { bar, seek } = setup();
    fireEvent.pointerDown(bar, { clientX: 10 });
    fireEvent.pointerMove(bar, { clientX: 30 });
    fireEvent.pointerUp(bar);
    fireEvent.pointerMove(bar, { clientX: 35 });
    expect(seek).toHaveBeenCalledTimes(2);
    expect(seek).toHaveBeenLastCalledWith(1.5);
  });

  it("семантика slider: подпись, границы и текст значения в секундах открытой части", () => {
    setup({ time: 0.5 });
    const bar = screen.getByRole("slider", { name: "Перемотка" });
    expect(bar).toHaveAttribute("aria-valuemin", "0");
    expect(bar).toHaveAttribute("aria-valuemax", "2");
    expect(bar).toHaveAttribute("aria-valuenow", "0.5");
    expect(bar).toHaveAttribute("aria-valuetext", "0,5 с из 2 с");
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("клавиатура: стрелки на шаг, Home и End к границам, не выходят за открытую часть", async () => {
    const user = userEvent.setup();
    const { bar, seek } = setup({ time: 1.8 });
    bar.focus();
    await user.keyboard("{ArrowRight}");
    expect(seek).toHaveBeenLastCalledWith(2);
    await user.keyboard("{ArrowLeft}");
    expect(seek).toHaveBeenLastCalledWith(1.3);
    await user.keyboard("{Home}");
    expect(seek).toHaveBeenLastCalledWith(0);
    await user.keyboard("{End}");
    expect(seek).toHaveBeenLastCalledWith(2);
  });

  it("отметка воспроизведения стоит по времени на линейной шкале, маркер уровня на месте", () => {
    setup({ time: 1 });
    expect(screen.getByTestId("tape-playhead").style.left).toBe("6.25%");
    expect(screen.getByTestId("tape-marker")).toHaveTextContent("2 с");
  });

  it("без перемотки (реверс, нет аудио) лента остаётся progressbar без отметки", () => {
    renderWithProviders(<TapeDeck weights={weights} levels={levels} level={3} progress={null} />);
    expect(screen.getByRole("progressbar")).toBeInTheDocument();
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tape-playhead")).not.toBeInTheDocument();
  });

  it("шкала без времени (равные шаги) не перематывается", () => {
    renderWithProviders(
      <TapeDeck
        weights={[1, 1, 1]}
        levels={[]}
        level={2}
        progress={null}
        seek={{ time: 0, open: 2, seek: vi.fn() }}
      />,
    );
    expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  });
});

describe("tapeSeekTarget", () => {
  it("линейно по времени и с прижимом к границе", () => {
    expect(tapeSeekTarget(160, RECT, 16, 8)).toBe(8);
    expect(tapeSeekTarget(200, RECT, 16, 8)).toBe(8);
    expect(tapeSeekTarget(-50, RECT, 16, 8)).toBe(0);
    expect(tapeSeekTarget(10, { left: 0, width: 0 }, 16, 8)).toBe(0);
  });
});

describe("стрелки на ленте не отвечают за игрока", () => {
  const me: MyRoundView = {
    attempts_left: 1,
    guesses: [],
    round_index: 0,
    status: "thinking",
    options: [
      { option_id: "a", title: "Первый" },
      { option_id: "b", title: "Второй" },
    ],
  };

  it("фокус на ленте: → перематывает и не отправляет ответ", async () => {
    const user = userEvent.setup();
    const onGuess = vi.fn();
    const seek = vi.fn();
    renderWithProviders(
      <>
        <TapeDeck
          weights={weights}
          levels={levels}
          level={3}
          progress={null}
          seek={{ time: 0, open: 2, seek }}
        />
        <PickMatchAnswer
          domain="music"
          me={me}
          disabled={false}
          canSkip={false}
          lastLevel={false}
          onGuess={onGuess}
          onSkip={vi.fn()}
        />
      </>,
    );
    screen.getByRole("slider").focus();
    await user.keyboard("{ArrowRight}");
    expect(seek).toHaveBeenCalledWith(0.5);
    expect(onGuess).not.toHaveBeenCalled();
  });
});
