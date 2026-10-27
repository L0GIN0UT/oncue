"use client";
/**
 * Вкладки раундов 1…N с цветом исхода: сыгранный раунд заливается цветом исхода (точно, почти, мимо,
 * пропуск), текущий — янтарём, будущие — пустые. Это индикатор, не переключатель: вернуться в
 * сыгранный раунд нельзя, а ответ раунда открывается модалкой.
 */
import { useTranslations } from "next-intl";
import type { RoundOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { roundOutcome } from "./levels";

type TabState = "exact" | "partial" | "miss" | "skip" | "current" | "upcoming";

const TONE: Record<TabState, string> = {
  exact: "border-exact-line bg-exact-fill text-exact",
  partial: "border-partial-line bg-partial-fill text-partial",
  miss: "border-miss-line bg-miss-fill text-miss",
  skip: "border-skip-line bg-skip-fill text-skip",
  current: "border-music bg-music text-night",
  upcoming: "border-rule bg-stage text-ink-faint",
};

/** Состояние вкладки: все попытки раунда пропуски — «пропуск», иначе исход раунда. */
export function tabState(round: RoundOut, current: number): TabState {
  const outcome = roundOutcome(round);
  if (outcome === "exact" || outcome === "partial") return outcome;
  if (outcome === "miss" || outcome === "void") {
    return round.guesses.every((g) => g.kind === "skip") ? "skip" : "miss";
  }
  return round.index === current ? "current" : "upcoming";
}

export function RoundTabs({ rounds, current }: { rounds: RoundOut[]; current: number }) {
  const t = useTranslations();
  return (
    <ol aria-label={t("game.roundsLabel")} data-testid="round-tabs" className="mb-1.5 flex gap-2">
      {rounds.map((round) => {
        const state = tabState(round, current);
        return (
          <li
            key={round.index}
            aria-label={t("game.roundTab", {
              index: round.index + 1,
              state: t(`game.roundState.${state}`),
            })}
            aria-current={state === "current" ? "step" : undefined}
            data-state={state}
            className={cn(
              "grid h-[34px] flex-1 place-items-center rounded-full border-[1.5px] text-[13px] font-semibold",
              TONE[state],
            )}
          >
            {round.index + 1}
          </li>
        );
      })}
    </ol>
  );
}
