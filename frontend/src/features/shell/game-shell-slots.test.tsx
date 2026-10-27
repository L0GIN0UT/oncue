import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GameShellSlots, GameShellSlotsProvider, useGameShellSlots } from "./game-shell-slots";

function Reader() {
  const { modeLabel, score } = useGameShellSlots();
  return (
    <p data-testid="read">
      {modeLabel}|{score}
    </p>
  );
}

describe("GameShellSlots", () => {
  it("отдаёт значения экрана шапке и очищает их при уходе экрана", () => {
    const { rerender } = render(
      <GameShellSlotsProvider>
        <GameShellSlots modeLabel="Дейлик" score="12" />
        <Reader />
      </GameShellSlotsProvider>,
    );
    expect(screen.getByTestId("read")).toHaveTextContent("Дейлик|12");
    rerender(
      <GameShellSlotsProvider>
        <Reader />
      </GameShellSlotsProvider>,
    );
    expect(screen.getByTestId("read")).toHaveTextContent("|");
    expect(screen.getByTestId("read")).not.toHaveTextContent("Дейлик");
  });
});
