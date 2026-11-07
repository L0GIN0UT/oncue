"use client";
/**
 * Лента времени игрового экрана: деления по уровням, заливка открытого отрезка, бегунок с
 * подписью («4 с» у загадок со временем, «2 из 6» у остальных) и светлая полоса воспроизведения
 * внутри заливки. Веса уровней задаёт конфиг режима (`tapeWeights`), клиент их не придумывает.
 */
import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type { LevelOut } from "@/shared/api/types";
import { Tape } from "@/shared/ui/tape";
import type { TapeSeekState } from "./clues/types";
import { isTimed, levelSeconds } from "./levels";

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
  /**
   * Перемотка по ленте: состояние плеера. Работает у загадок со временем и открытым аудио;
   * без него (реверс, нет аудио) лента остаётся индикатором.
   */
  seek?: TapeSeekState | null;
};

/** Шаг стрелок по ленте, с. */
const KEY_STEP_S = 0.5;

/** Секунды с точностью до десятой: уровень на 0,5 с не должен округляться до целой секунды. */
const tenths = (v: number) => Math.round(v * 10) / 10;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * Секунда под указателем: лента линейна по времени (`total` с), перемотка — только внутри
 * открытой части `[0, open]`, клик правее границы прижимается к ней.
 */
export function tapeSeekTarget(
  clientX: number,
  rect: { left: number; width: number },
  total: number,
  open: number,
): number {
  if (!(rect.width > 0) || !(total > 0)) return 0;
  const fraction = clamp((clientX - rect.left) / rect.width, 0, 1);
  return clamp(fraction * total, 0, Math.max(0, open));
}

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
  seek = null,
}: Props) {
  const t = useTranslations();
  const format = useFormatter();
  const bar = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const totalSeconds = total(weights);
  const seekable = seek !== null && isTimed(levels) && totalSeconds > 0;
  const open = seek ? clamp(seek.open, 0, totalSeconds) : 0;
  const now = seek ? clamp(seek.time, 0, open) : 0;

  const seekAt = (e: PointerEvent) => {
    const rect = bar.current?.getBoundingClientRect();
    if (rect && seek) seek.seek(tapeSeekTarget(e.clientX, rect, totalSeconds, open));
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (!seek || e.altKey || e.ctrlKey || e.metaKey) return;
    const next =
      e.key === "ArrowRight" || e.key === "ArrowUp"
        ? now + KEY_STEP_S
        : e.key === "ArrowLeft" || e.key === "ArrowDown"
          ? now - KEY_STEP_S
          : e.key === "Home"
            ? 0
            : e.key === "End"
              ? open
              : null;
    if (next === null) return;
    e.preventDefault();
    // Стрелки ленты не должны дойти до глобальных клавиш ответа (← → в паре вариантов).
    e.stopPropagation();
    seek.seek(clamp(next, 0, open));
  };
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
        barRef={bar}
        barProps={
          seekable
            ? {
                role: "slider",
                tabIndex: 0,
                "aria-label": t("game.seek.label"),
                // Сколько уровней открыто, лента теперь говорит описанием.
                "aria-description": t("game.tapeLabel", { level, total: weights.length }),
                "aria-valuemin": 0,
                "aria-valuemax": tenths(open),
                "aria-valuenow": tenths(now),
                "aria-valuetext": t("game.seek.valueText", {
                  current: tenths(now),
                  total: tenths(open),
                }),
                onKeyDown,
                onPointerDown: (e) => {
                  dragging.current = true;
                  e.currentTarget.setPointerCapture?.(e.pointerId);
                  seekAt(e);
                },
                onPointerMove: (e) => dragging.current && seekAt(e),
                onPointerUp: () => (dragging.current = false),
                onPointerCancel: () => (dragging.current = false),
                className:
                  "cursor-pointer touch-none select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-music",
              }
            : undefined
        }
      >
        {seekable && (
          <span
            aria-hidden
            data-testid="tape-playhead"
            className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-ink"
            style={{ left: `${(now / totalSeconds) * 100}%` }}
          />
        )}
      </Tape>
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
