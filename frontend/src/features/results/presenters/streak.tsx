"use client";
/** Итог серией (`presentation: streak`, «больше/меньше»): длина серии, рекорд и раунды. */
import { useTranslations } from "next-intl";
import { Flame } from "lucide-react";
import { RecordBadge, ResultRows } from "./common";
import type { Presenter, PresenterProps } from "./types";

function StreakHeader({ summary }: PresenterProps) {
  const t = useTranslations();
  return (
    <>
      <h1
        className="flex items-center justify-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule py-3 font-display"
        data-testid="final-score"
      >
        <Flame aria-hidden className="size-8 text-music" />
        <span className="text-base font-medium text-ink-muted">{t("results.streakLabel")}</span>
        <span className="tabular text-[40px] leading-none font-extrabold">{summary.score}</span>
      </h1>
      <RecordBadge record={summary.record} unit="streak" />
    </>
  );
}

export const streakPresenter: Presenter = { Header: StreakHeader, Body: ResultRows };
