"use client";
/**
 * Итог суммой очков (`presentation: score`): счёт 72 px из максимума, «N из M угадано», вкладки
 * раундов с цветом исхода и рекорд режима; ниже — строки раундов.
 */
import { useTranslations } from "next-intl";
import { OutcomeTabs, type TabState } from "@/features/game/RoundTabs";
import type { ResultRoundCard } from "@/shared/api/types";
import { RecordBadge, ResultRows } from "./common";
import type { Presenter, PresenterProps } from "./types";

const TAB_STATE: Record<ResultRoundCard["outcome"], TabState> = {
  exact: "exact",
  partial: "partial",
  miss: "miss",
  gave_up: "miss",
  void: "skip",
};

function ScoreHeader({ summary }: PresenterProps) {
  const t = useTranslations();
  const guessed = summary.rounds.filter((r) => r.outcome === "exact").length;
  return (
    <>
      <div className="flex flex-col items-center gap-1.5">
        <h1
          className="flex items-baseline justify-center gap-2 font-display"
          data-testid="final-score"
        >
          <span className="sr-only">{t("results.scoreLabel")}</span>
          <span className="tabular text-[72px] leading-none font-extrabold tracking-[-0.04em]">
            {summary.score}
          </span>
          <span className="tabular text-xl font-medium text-ink-faint">/ {summary.max_score}</span>
        </h1>
        <p className="text-[15px] text-ink-muted" data-testid="final-guessed">
          {t("results.guessed", { guessed, total: summary.rounds.length })}
        </p>
      </div>
      {summary.rounds.length > 1 && (
        <OutcomeTabs
          className="w-full"
          tabs={summary.rounds.map((r) => ({ index: r.index, state: TAB_STATE[r.outcome] }))}
        />
      )}
      <RecordBadge record={summary.record} unit="score" />
    </>
  );
}

export const scorePresenter: Presenter = { Header: ScoreHeader, Body: ResultRows };
