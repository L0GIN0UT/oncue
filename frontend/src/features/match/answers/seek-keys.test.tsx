import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@/test/render";
import { SeekBar } from "@/shared/ui/seek-bar";
import type { MyRoundView } from "@/shared/api/types";
import { PickMatchAnswer } from "./PickMatchAnswer";

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

function screenWithBar(onGuess = vi.fn(), onSeek = vi.fn()) {
  renderWithProviders(
    <>
      <SeekBar value={3} max={30} open={10} onSeek={onSeek} />
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
  return { onGuess, onSeek };
}

describe("стрелки на полосе перемотки не отвечают за игрока", () => {
  it("фокус на полосе: → перематывает и не отправляет ответ", async () => {
    const user = userEvent.setup();
    const { onGuess, onSeek } = screenWithBar();
    screen.getByRole("slider").focus();
    await user.keyboard("{ArrowRight}");
    expect(onSeek).toHaveBeenCalledWith(4);
    expect(onGuess).not.toHaveBeenCalled();
  });

  it("фокус не на полосе: → по-прежнему выбирает вариант", async () => {
    const user = userEvent.setup();
    const { onGuess, onSeek } = screenWithBar();
    await user.keyboard("{ArrowRight}");
    expect(onGuess).toHaveBeenCalledTimes(1);
    expect(onSeek).not.toHaveBeenCalled();
  });
});
