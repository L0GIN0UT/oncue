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
      <h1 className="flex items-center gap-4 font-display font-bold" data-testid="final-score">
        <Flame aria-hidden className="size-14 text-[var(--accent)] sm:size-16" />
        <span className="flex flex-col">
          <span className="text-lg font-semibold text-ink-muted">{t("results.streakLabel")}</span>
          <span className="tabular text-7xl leading-none sm:text-8xl">{summary.score}</span>
        </span>
      </h1>
      <RecordBadge record={summary.record} unit="streak" />
    </>
  );
}

export const streakPresenter: Presenter = { Header: StreakHeader, Body: ResultRows };
