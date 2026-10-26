"use client";
/**
 * Строки попыток раунда: по строке на уровень, сделанная заливается цветом исхода целиком,
 * текущая — рамка с свечением, остальные — пустые. Появление строки анимируется только у попыток,
 * сделанных на этом экране (при загрузке сессии строки появляются сразу).
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import type { GuessOut } from "@/shared/api/types";
import { AttemptRow, type AttemptStatus } from "@/shared/ui/attempt-row";

type GuessKey = "exact" | "partial" | "miss" | "skip";

const guessKey = (g: GuessOut): GuessKey => (g.kind === "skip" ? "skip" : (g.result ?? "miss"));

export function AttemptRows({
  guesses,
  levelsCount,
  active,
  testId = "attempts",
}: {
  guesses: GuessOut[];
  levelsCount: number;
  /** Раунд идёт: следующая после сделанных строка — текущая попытка. */
  active: boolean;
  testId?: string;
}) {
  const t = useTranslations();
  const [initial] = useState(guesses.length);
  return (
    <div
      role="list"
      aria-label={t("game.historyLabel")}
      data-testid={testId}
      className="flex flex-col gap-2.5"
    >
      {Array.from({ length: levelsCount }, (_, i) => {
        const g = guesses[i];
        let status: AttemptStatus = "empty";
        if (g) status = guessKey(g);
        else if (active && i === guesses.length) status = "current";
        return (
          <div key={i} role="listitem" className="contents">
            <AttemptRow
              status={status}
              statusLabel={g ? t(`game.guess.${guessKey(g)}`) : undefined}
              hint={t("game.partialHint")}
              animate={Boolean(g) && i >= initial}
            >
              {g ? (g.kind === "skip" ? t("game.skipped") : g.display) : null}
            </AttemptRow>
          </div>
        );
      })}
    </div>
  );
}
