"use client";
/** История попыток раунда: строка на уровень (✗ мимо, ≈ частично, ✓ верно, пропуск). */
import { useTranslations } from "next-intl";
import { Check, Minus, SkipForward, X } from "lucide-react";
import type { GuessOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";

const ICON = {
  exact: { Icon: Check, tone: "text-exact" },
  partial: { Icon: Minus, tone: "text-partial" },
  miss: { Icon: X, tone: "text-miss" },
  skip: { Icon: SkipForward, tone: "text-ink-faint" },
} as const;

export function AttemptHistory({
  guesses,
  levelsCount,
}: {
  guesses: GuessOut[];
  levelsCount: number;
}) {
  const t = useTranslations();
  return (
    <ol
      className="flex flex-col gap-1.5"
      aria-label={t("game.historyLabel")}
      data-testid="attempts"
    >
      {Array.from({ length: levelsCount }, (_, i) => {
        const g = guesses[i];
        if (!g) {
          return (
            <li
              key={i}
              aria-hidden
              className="h-10 rounded-lg border border-dashed border-rule/80"
            />
          );
        }
        const key = g.kind === "skip" ? "skip" : (g.result ?? "miss");
        const { Icon, tone } = ICON[key];
        return (
          <li
            key={i}
            className="flex h-10 items-center gap-3 rounded-lg bg-stage-raised px-3"
            data-result={key}
          >
            <Icon className={cn("size-4 shrink-0", tone)} aria-hidden />
            <span className="sr-only">{t(`game.guess.${key}`)}:</span>
            <span className={cn("truncate", key === "skip" && "text-ink-faint")}>
              {g.kind === "skip" ? t("game.skipped") : g.display}
            </span>
            {key === "partial" && (
              <span className="ml-auto shrink-0 text-xs text-partial">{t("game.partialHint")}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
