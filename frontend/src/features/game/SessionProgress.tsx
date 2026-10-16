"use client";
/**
 * Прогресс сессии из `progress` раунда: жизни (марафон, пара), серия (пара) и тур сетки.
 * Показывается то, что прислал сервер (`null` — поля нет у режима); клиент ничего не считает.
 */
import { useTranslations } from "next-intl";
import { Flame, Heart } from "lucide-react";
import type { RoundOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { bracketStages, pairInStage, stageLabel } from "./bracket";

export function SessionProgress({ rounds, round }: { rounds: RoundOut[]; round: RoundOut }) {
  const t = useTranslations();
  const progress = round.progress ?? null;
  if (!progress) return null;
  const lives = progress.lives_left;
  const streak = progress.streak;
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
      {lives !== null && lives !== undefined && (
        <p className="flex items-center gap-2" data-testid="lives">
          <span className="text-sm text-ink-muted">{t("game.lives")}</span>
          <span aria-hidden className="flex gap-1 text-[var(--accent)]">
            {Array.from({ length: Math.min(lives, 10) }, (_, i) => (
              <Heart key={i} className="size-4 fill-current" />
            ))}
          </span>
          <span className="tabular font-display text-lg font-semibold">{lives}</span>
        </p>
      )}
      {streak !== null && streak !== undefined && (
        <p className="flex items-center gap-2" data-testid="streak">
          <Flame aria-hidden className="size-4 text-[var(--accent)]" />
          <span className="text-sm text-ink-muted">{t("game.streak")}</span>
          <span className="tabular font-display text-lg font-semibold">{streak}</span>
        </p>
      )}
      {progress.stage ? <BracketProgress rounds={rounds} round={round} /> : null}
    </div>
  );
}

/** Туры сетки 1/16 → финал: текущий подсвечен, рядом — номер пары в туре. */
export function BracketProgress({ rounds, round }: { rounds: RoundOut[]; round: RoundOut }) {
  const t = useTranslations();
  const stage = round.progress?.stage;
  if (!stage) return null;
  const stages = bracketStages(rounds[0]?.progress?.stage ?? stage);
  const pair = pairInStage(rounds, round);
  const label = (s: number) => {
    const { key, ...values } = stageLabel(s);
    return t(key, values);
  };
  return (
    <div className="flex flex-wrap items-center gap-3" data-testid="bracket-stage">
      <p className="font-display text-lg font-semibold">
        {label(stage)}
        {pair && (
          <span className="ml-2 text-sm font-normal text-ink-muted">
            {t("game.bracket.pair", pair)}
          </span>
        )}
      </p>
      <ol className="flex gap-1.5" aria-label={t("game.bracket.stagesLabel")}>
        {stages.map((s) => (
          <li
            key={s}
            aria-current={s === stage ? "step" : undefined}
            className={cn(
              "rounded-full px-2.5 py-0.5 text-xs ring-1 ring-rule ring-inset",
              s === stage
                ? "bg-[var(--accent)] text-night ring-transparent"
                : s > stage
                  ? "text-ink-muted line-through decoration-rule"
                  : "text-ink-muted",
            )}
          >
            {label(s)}
          </li>
        ))}
      </ol>
    </div>
  );
}
