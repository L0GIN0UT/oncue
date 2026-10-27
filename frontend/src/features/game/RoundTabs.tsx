"use client";
/**
 * Вкладки раундов 1…N с цветом исхода: сыгранный раунд заливается цветом исхода (точно, почти, мимо,
 * пропуск), текущий — янтарём, будущие — пустые. Это индикатор, не переключатель: вернуться в
 * сыгранный раунд нельзя, а ответ раунда открывается модалкой. Те же вкладки — в итогах.
 */
import { useTranslations } from "next-intl";
import type { RoundOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { roundOutcome } from "./levels";

export type TabState = "exact" | "partial" | "miss" | "skip" | "current" | "upcoming";

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

export type OutcomeTab = { index: number; state: TabState };

/** Ряд вкладок по готовым состояниям (используют экран раунда и итоги). */
export function OutcomeTabs({ tabs, className }: { tabs: OutcomeTab[]; className?: string }) {
  const t = useTranslations();
  return (
    <ol
      aria-label={t("game.roundsLabel")}
      data-testid="round-tabs"
      className={cn("flex gap-2", className)}
    >
      {tabs.map(({ index, state }) => (
        <li
          key={index}
          aria-label={t("game.roundTab", {
            index: index + 1,
            state: t(`game.roundState.${state}`),
          })}
          aria-current={state === "current" ? "step" : undefined}
          data-state={state}
          className={cn(
            "grid h-[34px] flex-1 place-items-center rounded-full border-[1.5px] text-[13px] font-semibold",
            TONE[state],
          )}
        >
          {index + 1}
        </li>
      ))}
    </ol>
  );
}

export function RoundTabs({ rounds, current }: { rounds: RoundOut[]; current: number }) {
  return (
    <OutcomeTabs
      className="mb-1.5"
      tabs={rounds.map((round) => ({ index: round.index, state: tabState(round, current) }))}
    />
  );
}
