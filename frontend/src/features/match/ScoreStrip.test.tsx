import { describe, expect, it } from "vitest";
import { renderHook, screen } from "@testing-library/react";
import { setupMockBackend, signInAsGuest } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { MatchPlayerView, PlayerSlot } from "@/shared/api/types";
import type { RoundRevealDelta } from "@/shared/realtime";
import { PlayersRail } from "./PlayersRail";
import { ScoreStrip, useRoundWinners } from "./ScoreStrip";

setupMockBackend();

const ME = "00000000-0000-4000-8000-000000000001";
const RIVAL = "00000000-0000-4000-8000-000000000002";

const slot = (user_id: string, nickname: string, n: number, over: Partial<PlayerSlot> = {}) =>
  ({
    user_id,
    nickname,
    slot: n,
    score: 0,
    is_host: n === 0,
    presence: "online",
    ready: true,
    away: false,
    ...over,
  }) as PlayerSlot;

const reveal = (round_index: number, mine: number, rival: number) =>
  ({
    round_index,
    results: [
      { user_id: ME, level: 1, partial: false, score: mine, total: mine },
      { user_id: RIVAL, level: 1, partial: false, score: rival, total: rival },
    ],
  }) as unknown as RoundRevealDelta;

describe("useRoundWinners", () => {
  it("копит победителей раундов: больше очков — победа, поровну — ничья", () => {
    const { result, rerender } = renderHook(({ r }) => useRoundWinners(r), {
      initialProps: { r: null as RoundRevealDelta | null },
    });
    expect(result.current.size).toBe(0);
    rerender({ r: reveal(0, 10, 4) });
    rerender({ r: reveal(1, 3, 3) });
    rerender({ r: reveal(2, 0, 7) });
    expect([...result.current]).toEqual([
      [0, ME],
      [1, null],
      [2, RIVAL],
    ]);
  });
});

describe("ScoreStrip", () => {
  const players = [slot(ME, "player", 0), slot(RIVAL, "rival", 1)];
  const matchPlayers = [
    { user_id: ME, score: 12, status: "thinking" },
    { user_id: RIVAL, score: 8, status: "answered" },
  ] as MatchPlayerView[];

  it("счёт посередине, раунд, точки раундов у каждого игрока", () => {
    signInAsGuest();
    renderWithProviders(
      <ScoreStrip
        players={players}
        matchPlayers={matchPlayers}
        myId={ME}
        roundIndex={2}
        roundsTotal={5}
        winners={
          new Map([
            [0, ME],
            [1, RIVAL],
          ])
        }
        bubbles={new Map()}
      />,
    );
    expect(screen.getByTestId("duel-score")).toHaveTextContent("12 : 8");
    expect(screen.getByText("раунд 3 из 5")).toBeInTheDocument();
    const mine = screen.getByTestId("player-player");
    expect(mine).toHaveTextContent("Ты");
    expect(mine.querySelector('[data-result="won"]')).not.toBeNull();
    expect(mine.querySelector('[data-result="lost"]')).not.toBeNull();
    expect(mine.querySelectorAll('[data-result="pending"]')).toHaveLength(3);
    const rival = screen.getByTestId("player-rival");
    expect(rival.querySelector('[data-result="won"]')).not.toBeNull();
    expect(
      screen.getByTestId("player-rival").querySelector('[data-testid="player-status"]'),
    ).toHaveTextContent("Ответил");
  });
});

describe("PlayersRail", () => {
  it("свободные места лобби — пунктирные плашки вне дерева доступности", () => {
    signInAsGuest();
    renderWithProviders(
      <PlayersRail players={[slot(ME, "player", 0)]} myId={ME} seats={3} showReady />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByTestId("ready-state")).toHaveTextContent("Готов");
  });
});
