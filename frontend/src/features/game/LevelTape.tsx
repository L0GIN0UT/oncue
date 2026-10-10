"use client";
/**
 * Лента уровней — главный элемент игрового экрана. Каждый отрезок — уровень загадки; ширина
 * пропорциональна приросту длительности (для загадок со временем) или равна (кадры).
 * Открытые уровни подсвечены цветом домена, пройденные — цветом исхода попытки,
 * поверх идёт бегунок воспроизведения.
 */
import { useTranslations } from "next-intl";
import type { GuessOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";

type Props = {
  weights: number[];
  /** Текущий открытый уровень (1-based). */
  level: number;
  guesses: GuessOut[];
  /** Доля воспроизведения 0..1 от всей ленты; `null` — бегунка нет. */
  progress?: number | null;
  className?: string;
};

const RESULT_CLASS: Record<string, string> = {
  exact: "bg-exact",
  partial: "bg-partial",
  miss: "bg-miss",
  skip: "bg-skip",
};

export function LevelTape({ weights, level, guesses, progress = null, className }: Props) {
  const t = useTranslations();
  return (
    <div className={cn("relative", className)}>
      <ol
        className="flex h-4 gap-1"
        aria-label={t("game.tapeLabel", { level, total: weights.length })}
      >
        {weights.map((w, i) => {
          const index = i + 1;
          const guess = guesses[i];
          const tone = guess
            ? RESULT_CLASS[guess.kind === "skip" ? "skip" : (guess.result ?? "miss")]
            : null;
          return (
            <li
              key={index}
              style={{ flexGrow: w, flexBasis: 0 }}
              className={cn(
                "min-w-2 rounded-[3px] transition-colors duration-300",
                tone ??
                  (index === level
                    ? "bg-[var(--accent)]"
                    : index < level
                      ? "bg-[var(--accent)]/60"
                      : "bg-rule"),
              )}
              aria-current={index === level ? "step" : undefined}
            >
              <span className="sr-only">
                {guess
                  ? t(`game.guess.${guess.kind === "skip" ? "skip" : (guess.result ?? "miss")}`)
                  : index <= level
                    ? t("game.levelOpen", { level: index })
                    : t("game.levelLocked", { level: index })}
              </span>
            </li>
          );
        })}
      </ol>
      {progress !== null && (
        <span
          aria-hidden
          data-testid="playhead"
          className="pointer-events-none absolute -top-1.5 h-7 w-0.5 rounded-full bg-ink shadow-[0_0_0_3px_var(--color-night)]"
          style={{ left: `${Math.min(1, Math.max(0, progress)) * 100}%` }}
        />
      )}
    </div>
  );
}

/** Сумма весов — для перевода секунд воспроизведения в долю ленты. */
export function tapeTotal(weights: number[]): number {
  return weights.reduce((a, b) => a + b, 0);
}
