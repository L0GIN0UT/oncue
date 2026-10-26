import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AttemptRow, type AttemptStatus } from "./attempt-row";
import { PlayButton } from "./play-button";
import { Tape, tickPositions } from "./tape";

describe("tickPositions", () => {
  it("деления на накопленных границах уровней, без края ленты", () => {
    expect(tickPositions([1, 1, 2])).toEqual([0.25, 0.5]);
    expect(tickPositions([])).toEqual([]);
    expect(tickPositions([0, 0])).toEqual([]);
  });
});

describe("Tape", () => {
  it("заливка и бегунок с подписью секунд", () => {
    render(
      <Tape
        aria-label="Лента"
        weights={[1, 1, 2, 3, 4]}
        filled={0.25}
        marker={{ at: 0.25, label: "4 с" }}
      />,
    );
    const bar = screen.getByRole("progressbar", { name: "Лента" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByTestId("tape-marker")).toHaveTextContent("4 с");
    expect(screen.getByTestId("tape-marker").style.left).toBe("25%");
    // Деления: по одному на границу уровней (их пять, считая заливку: четыре деления + заливка).
    expect(bar.querySelectorAll("span[aria-hidden]")).toHaveLength(4);
  });

  it("значения вне диапазона обрезаются, метка соперника отдельно", () => {
    render(
      <Tape
        aria-label="Лента"
        weights={[1, 1]}
        filled={3}
        opponent={{ at: -1, label: "соперник" }}
      />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByTestId("tape-opponent").style.left).toBe("0%");
    expect(screen.queryByTestId("tape-marker")).not.toBeInTheDocument();
  });
});

describe("AttemptRow", () => {
  const cases: [AttemptStatus, string][] = [
    ["exact", "bg-exact-fill"],
    ["partial", "bg-partial-fill"],
    ["miss", "bg-miss-fill"],
    ["skip", "bg-skip-fill"],
    ["current", "border-music"],
    ["empty", "border-rule"],
  ];
  it.each(cases)("%s красится своим токеном", (status, cls) => {
    const { container } = render(<AttemptRow status={status}>Radiohead — Creep</AttemptRow>);
    const row = container.firstElementChild!;
    expect(row.className).toContain(cls);
    expect(row).toHaveAttribute("data-result", status);
  });

  it("исход доступен скринридеру, у частичного есть подпись справа", () => {
    render(
      <AttemptRow status="partial" statusLabel="Исполнитель верный" hint="исполнитель">
        Karma Police
      </AttemptRow>,
    );
    expect(screen.getByText("Исполнитель верный:")).toHaveClass("sr-only");
    expect(screen.getByText("исполнитель")).toBeInTheDocument();
  });

  it("пустая строка скрыта от скринридера, появление анимируется только по флагу", () => {
    const { container, rerender } = render(<AttemptRow status="empty" />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(container.firstElementChild!.className).not.toContain("animate-row-in");
    rerender(<AttemptRow status="miss" animate />);
    expect(container.firstElementChild!.className).toContain("animate-row-in");
  });
});

describe("PlayButton", () => {
  it("состояния: ожидание и воспроизведение (пульс только в нём)", async () => {
    const onClick = vi.fn();
    const { rerender } = render(<PlayButton aria-label="Слушать" onClick={onClick} />);
    const btn = screen.getByRole("button", { name: "Слушать" });
    expect(btn).toHaveAttribute("aria-pressed", "false");
    expect(btn.className).not.toContain("animate-pulse-ring");
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
    rerender(<PlayButton aria-label="Пауза" playing />);
    expect(screen.getByRole("button", { name: "Пауза" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Пауза" }).className).toContain("animate-pulse-ring");
  });

  it("disabled не реагирует", () => {
    render(<PlayButton aria-label="Слушать" disabled />);
    expect(screen.getByRole("button")).toBeDisabled();
  });
});
