"use client";
/** Итог суммой очков (`presentation: score`): счёт из максимума и строки раундов. */
import { useTranslations } from "next-intl";
import { RecordBadge, ResultRows } from "./common";
import type { Presenter, PresenterProps } from "./types";

function ScoreHeader({ summary }: PresenterProps) {
  const t = useTranslations();
  return (
    <>
      <h1
        className="flex items-baseline justify-center gap-2 rounded-[var(--radius-control)] border-[1.5px] border-rule py-3 font-display"
        data-testid="final-score"
      >
        <span className="sr-only">{t("results.scoreLabel")}</span>
        <span className="tabular text-[40px] leading-none font-extrabold">{summary.score}</span>
        <span className="tabular text-xl text-ink-faint">/ {summary.max_score}</span>
      </h1>
      <RecordBadge record={summary.record} unit="score" />
    </>
  );
}

export const scorePresenter: Presenter = { Header: ScoreHeader, Body: ResultRows };
