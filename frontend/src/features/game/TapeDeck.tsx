"use client";
/**
 * Лента времени игрового экрана: деления по уровням, заливка открытого отрезка, бегунок с
 * подписью («4 с» у загадок со временем, «2 из 6» у остальных) и светлая полоса воспроизведения
 * внутри заливки. Веса уровней задаёт конфиг режима (`tapeWeights`), клиент их не придумывает.
 */
import { useFormatter, useTranslations } from "next-intl";
import type { LevelOut } from "@/shared/api/types";
import { Tape } from "@/shared/ui/tape";
import { levelSeconds } from "./levels";

type Props = {
  weights: readonly number[];
  levels: LevelOut[];
  /** Открытый уровень (1-based). */
  level: number;
  /** Доля воспроизведения 0..1 от всей ленты; `null` — не играет. */
  progress: number | null;
  /** Серая метка соперника в дуэли: уровень, до которого он дошёл (1-based). */
  opponentLevel?: number | null;
  opponentLabel?: string;
};

const total = (weights: readonly number[]) => weights.reduce((a, b) => a + b, 0);

/** Доля ленты, закрытая уровнями `1..level`. */
export function levelShare(weights: readonly number[], level: number): number {
  const sum = total(weights);
  if (sum <= 0) return 0;
  return weights.slice(0, level).reduce((a, b) => a + b, 0) / sum;
}

export function TapeDeck({
  weights,
  levels,
  level,
  progress,
  opponentLevel = null,
  opponentLabel,
}: Props) {
  const t = useTranslations();
  const format = useFormatter();
  const filled = levelShare(weights, level);
  const seconds = levelSeconds(levels, level);
  const label =
    seconds !== null
      ? t("game.tapeSeconds", { seconds: format.number(seconds, { maximumFractionDigits: 1 }) })
      : t("game.tapeStep", { level, total: weights.length });
  return (
    <div className="relative">
      <Tape
        weights={weights}
        filled={filled}
        marker={{ at: filled, label }}
        opponent={
          opponentLevel !== null
            ? { at: levelShare(weights, opponentLevel), label: opponentLabel ?? "" }
            : null
        }
        aria-label={t("game.tapeLabel", { level, total: weights.length })}
      />
      {progress !== null && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-3.5 overflow-hidden rounded-[7px]"
        >
          <span
            data-testid="playhead"
            className="block h-full bg-ink/35"
            style={{ width: `${Math.min(1, Math.max(0, progress)) * 100}%` }}
          />
        </span>
      )}
    </div>
  );
}
