/** Итог по `presentation` (реестр презентеров) на мок-бэкенде. */
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { signInAsGuest, setupMockBackend, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { personId, type MockSession } from "../../../e2e/mocks/world";
import { ResultsView } from "./ResultsView";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

setupMockBackend();

/** Доиграть сессию в мок-мире: варианты — первым, классика — сдачей. */
function playOut(s: MockSession): void {
  for (let guard = 0; s.status === "active" && guard < 50; guard++) {
    const r = s.rounds.find((x) => x.index === s.current)!;
    if (r.options.length) world.guess(s, r, { option_id: r.options[0]!.option_id });
    else world.giveUp(s, r);
  }
}

/** Вход пользователем мока (рекорды — только у пользователей). */
function signInAsUser(): string {
  const subject = `user.${world.users[0]!.id}`;
  document.cookie = `gv_access=${subject}; Path=/`;
  document.cookie = `gv_csrf=csrf-user; Path=/`;
  return subject;
}

describe("ResultsView по presentation", () => {
  it("score: счёт из максимума и строка на каждый сыгранный раунд марафона", async () => {
    const m = world.startSession(signInAsGuest(), world.mode("film.marathon.director")!, null, [
      personId("Андрей Тарковский"),
    ]);
    playOut(m);
    renderWithProviders(<ResultsView sessionId={m.id} />);
    expect(await screen.findByTestId("final-score")).toHaveTextContent("0/ 18");
    // Три сдачи — три жизни: три раунда из пяти.
    expect(screen.getAllByTestId("result-row")).toHaveLength(3);
    expect(screen.queryByTestId("record")).not.toBeInTheDocument();
  });

  it("streak: длина серии, рекорд пользователя с процентилем", async () => {
    const s = world.startSession(signInAsUser(), world.mode("film.pair.rating")!, null);
    playOut(s);
    renderWithProviders(<ResultsView sessionId={s.id} />);
    const score = await screen.findByTestId("final-score");
    expect(score).toHaveTextContent(/Серия\s*\d+/);
    expect(screen.getByTestId("record")).toHaveTextContent("Лучшая серия:");
    expect(screen.getByTestId("record-percentile")).toHaveTextContent("лучше, чем у 73% игроков");
  });

  it("bracket: чемпион и выборы по турам, финал первым", async () => {
    const s = world.startSession(signInAsGuest(), world.mode("film.bracket.weekly")!, null);
    playOut(s);
    renderWithProviders(<ResultsView sessionId={s.id} />);
    const champion = await screen.findByTestId("bracket-champion");
    expect(champion).toHaveTextContent(world.result(s).bracket!.champion);
    expect(screen.getAllByTestId("bracket-pick")).toHaveLength(7);
    const stages = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(stages).toEqual(["Финал", "Полуфинал", "1/4 финала"]);
    expect(screen.queryByTestId("result-row")).not.toBeInTheDocument();
  });
});

describe("ResultsView: следующий шаг", () => {
  it("после дейлика музыки предлагает дейлик кино, вкладки и «открыто N с» в строках", async () => {
    const s = world.startSession(signInAsGuest(), world.mode("music.daily.classic")!, null);
    playOut(s);
    renderWithProviders(<ResultsView sessionId={s.id} />);
    const next = await screen.findByTestId("results-next");
    await waitFor(() => expect(next).toHaveTextContent("Теперь кино"));
    expect(next).toHaveAttribute("href", "/play/film.daily.frame");
    expect(screen.getByTestId("round-tabs").querySelectorAll("li")).toHaveLength(5);
    expect(screen.getByTestId("final-guessed")).toHaveTextContent("очков · 0 из 5 угадано");
    expect(screen.getAllByTestId("result-row")).toHaveLength(5);
  });

  it("соло-режим не из дейликов ведёт к режимам", async () => {
    const m = world.startSession(signInAsGuest(), world.mode("film.marathon.director")!, null, [
      personId("Андрей Тарковский"),
    ]);
    playOut(m);
    renderWithProviders(<ResultsView sessionId={m.id} />);
    const next = await screen.findByTestId("results-next");
    expect(next).toHaveTextContent("Другие режимы");
    expect(next).toHaveAttribute("href", "/solo");
  });
});
