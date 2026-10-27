import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsGuest } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { GuessOut, RoundOut } from "@/shared/api/types";
import { AttemptRows } from "./AttemptRows";
import { AnswerBox } from "./AnswerBox";
import { RevealDialog } from "./RevealDialog";
import { TapeDeck, levelShare } from "./TapeDeck";

setupMockBackend();

const guess = (kind: GuessOut["kind"], result: GuessOut["result"], display = "x"): GuessOut => ({
  kind,
  result,
  display,
});

describe("AttemptRows", () => {
  it("строка на уровень: исходы заливкой, текущая со свечением, остальные пустые", () => {
    renderWithProviders(
      <AttemptRows
        levelsCount={6}
        active
        guesses={[
          guess("text", "miss", "Muse — Hysteria"),
          guess("skip", null),
          guess("item", "partial", "Radiohead — Karma Police"),
        ]}
      />,
    );
    const rows = screen.getByTestId("attempts").querySelectorAll("[data-result]");
    expect([...rows].map((r) => r.getAttribute("data-result"))).toEqual([
      "miss",
      "skip",
      "partial",
      "current",
      "empty",
      "empty",
    ]);
    expect(screen.getByText("Muse — Hysteria")).toBeInTheDocument();
    expect(screen.getByText("Пропуск")).toBeInTheDocument();
    expect(screen.getByText("исполнитель верный")).toBeInTheDocument();
  });

  it("у завершённого раунда текущей строки нет", () => {
    renderWithProviders(
      <AttemptRows levelsCount={3} active={false} guesses={[guess("item", "exact", "Creep")]} />,
    );
    expect(screen.getByTestId("attempts").querySelector('[data-result="current"]')).toBeNull();
  });
});

describe("TapeDeck", () => {
  const levels = [0.5, 1, 2, 4, 8, 16].map((seconds, i) => ({
    level: i + 1,
    hint: { seconds },
  })) as never;
  const weights = [0.5, 0.5, 1, 2, 4, 8];

  it("бегунок стоит на конце открытого уровня и подписан секундами", () => {
    renderWithProviders(<TapeDeck weights={weights} levels={levels} level={3} progress={null} />);
    expect(screen.getByTestId("tape-marker")).toHaveTextContent("2 с");
    expect(levelShare(weights, 3)).toBeCloseTo(2 / 16);
    expect(screen.queryByTestId("playhead")).not.toBeInTheDocument();
  });

  it("при воспроизведении внутри заливки идёт светлая полоса", () => {
    renderWithProviders(<TapeDeck weights={weights} levels={levels} level={3} progress={0.05} />);
    expect(screen.getByTestId("playhead").style.width).toBe("5%");
  });

  it("у загадок без времени подпись «N из M»", () => {
    renderWithProviders(<TapeDeck weights={[1, 1, 1]} levels={[]} level={2} progress={null} />);
    expect(screen.getByTestId("tape-marker")).toHaveTextContent("2 из 3");
  });
});

describe("AnswerBox", () => {
  it("«Пропустить», пока ответа нет, и «Ответить», когда есть текст; крестик очищает", async () => {
    signInAsGuest();
    const user = userEvent.setup();
    const onSkip = vi.fn();
    const onAnswer = vi.fn();
    renderWithProviders(
      <AnswerBox
        domain="film"
        disabled={false}
        onAnswer={onAnswer}
        skip={{ label: "Пропустить", onSkip, testId: "skip" }}
      />,
    );
    await user.click(screen.getByTestId("skip"));
    expect(onSkip).toHaveBeenCalledTimes(1);

    await user.type(screen.getByRole("combobox"), "совсем не то");
    expect(screen.queryByTestId("skip")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Очистить" }));
    expect(screen.getByRole("combobox")).toHaveValue("");
    expect(screen.getByTestId("skip")).toBeInTheDocument();

    await user.type(screen.getByRole("combobox"), "совсем не то");
    await user.click(screen.getByRole("button", { name: "Ответить" }));
    expect(onAnswer).toHaveBeenCalledWith({ text: "совсем не то" });
  });
});

const finishedRound = (over: Partial<RoundOut> = {}): RoundOut => ({
  index: 2,
  level: 3,
  levels_count: 6,
  status: "finished",
  points: 4,
  guesses: [guess("text", "miss"), guess("skip", null), guess("item", "exact", "Creep")],
  reveal: {
    media_item_id: "00000000-0000-4000-8000-000000000001",
    title: "Creep",
    year: 1992,
    persons: [{ name: "Radiohead", role: "artist" }],
    final_clue: {
      kind: "audio",
      url: "blob:x",
      mime: "audio/mpeg",
      expires_at: "2099-01-01T00:00:00Z",
    },
  },
  ...over,
});

describe("RevealDialog", () => {
  const base = { sessionId: "s", presentation: "score" as const, stats: null, last: false };

  it("угадал: янтарная шапка, название, плитки «Попытка» и «Очки», сохранение по домену", () => {
    signInAsGuest();
    renderWithProviders(
      <RevealDialog {...base} round={finishedRound()} onNext={vi.fn()} domain="music" />,
    );
    expect(screen.getByTestId("reveal-outcome")).toHaveTextContent("Угадал с 3-й");
    expect(screen.getByTestId("reveal-outcome")).toHaveClass("bg-music");
    expect(screen.getByRole("heading", { name: "Creep" })).toBeInTheDocument();
    expect(screen.getByText("Radiohead · 1992")).toBeInTheDocument();
    expect(screen.getByText("3 из 6")).toBeInTheDocument();
    expect(screen.getByText("+4")).toBeInTheDocument();
    expect(screen.queryByText("Слушать отрывок")).not.toBeInTheDocument();
  });

  it("у кино кнопка сохранения «В закладки», у музыки «В плейлист»", () => {
    signInAsGuest();
    const { unmount } = renderWithProviders(
      <RevealDialog {...base} round={finishedRound()} onNext={vi.fn()} domain="film" />,
    );
    expect(screen.getByTestId("save-button")).toHaveTextContent("В закладки");
    unmount();
    renderWithProviders(
      <RevealDialog {...base} round={finishedRound()} onNext={vi.fn()} domain="music" />,
    );
    expect(screen.getByTestId("save-button")).toHaveTextContent("В плейлист");
  });

  it("пропуск на каждом уровне: шапка «Пропущено», попытка — прочерк", () => {
    signInAsGuest();
    renderWithProviders(
      <RevealDialog
        {...base}
        last={false}
        round={finishedRound({ points: 0, guesses: [guess("skip", null), guess("skip", null)] })}
        onNext={vi.fn()}
        domain="music"
      />,
    );
    expect(screen.getByTestId("reveal-outcome")).toHaveTextContent("Пропущено");
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("промах: серая шапка; последний раунд — «К итогам»; Escape и пробел продолжают", async () => {
    signInAsGuest();
    const user = userEvent.setup();
    const onNext = vi.fn();
    renderWithProviders(
      <RevealDialog
        {...base}
        last
        round={finishedRound({ points: 0, guesses: [guess("text", "miss")] })}
        onNext={onNext}
        domain="music"
      />,
    );
    expect(screen.getByTestId("reveal-outcome")).toHaveTextContent("Не в этот раз");
    expect(screen.getByTestId("reveal-outcome")).toHaveClass("bg-rule");
    expect(screen.getByTestId("reveal-next")).toHaveTextContent("К итогам");
    expect(screen.getByTestId("reveal-next")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onNext).toHaveBeenCalledTimes(1);
  });
});
