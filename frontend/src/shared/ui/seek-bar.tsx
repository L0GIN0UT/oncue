"use client";
/**
 * Полоса перемотки аудио: клик и перетаскивание по полосе, стрелки с клавиатуры. Полоса —
 * весь фрагмент (`max`, с), а доступна только его открытая часть (`open`, с): клик дальше
 * границы прижимается к ней, так что услышать больше, чем разрешает уровень, нельзя.
 */
import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";

/** Шаг стрелок и постраничный шаг, с. */
const STEP_S = 1;
const PAGE_S = 5;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Секунда под указателем: позиция по полосе → время, прижатое к открытой части `[0, open]`. */
export function seekTarget(
  clientX: number,
  rect: { left: number; width: number },
  max: number,
  open: number,
): number {
  if (!(rect.width > 0) || !(max > 0)) return 0;
  const fraction = clamp((clientX - rect.left) / rect.width, 0, 1);
  return clamp(fraction * max, 0, Math.max(0, open));
}

type Props = {
  /** Текущая позиция, с. */
  value: number;
  /** Длина всей полосы, с. */
  max: number;
  /** Открытая часть полосы, с: дальше перематывать нельзя. */
  open: number;
  onSeek: (seconds: number) => void;
  className?: string;
};

export function SeekBar({ value, max, open, onSeek, className }: Props) {
  const t = useTranslations();
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const limit = clamp(open, 0, max);

  const seekAt = (e: PointerEvent) => {
    const rect = track.current?.getBoundingClientRect();
    if (rect) onSeek(seekTarget(e.clientX, rect, max, limit));
  };

  const onKeyDown = (e: KeyboardEvent) => {
    const next =
      e.key === "ArrowRight" || e.key === "ArrowUp"
        ? value + STEP_S
        : e.key === "ArrowLeft" || e.key === "ArrowDown"
          ? value - STEP_S
          : e.key === "PageUp"
            ? value + PAGE_S
            : e.key === "PageDown"
              ? value - PAGE_S
              : e.key === "Home"
                ? 0
                : e.key === "End"
                  ? limit
                  : null;
    if (next === null || e.altKey || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    // Стрелки полосы не должны дойти до глобальных клавиш ответа (← → в паре вариантов).
    e.stopPropagation();
    onSeek(clamp(next, 0, limit));
  };

  const now = clamp(value, 0, limit);
  const pct = (s: number) => `${max > 0 ? (clamp(s, 0, max) / max) * 100 : 0}%`;
  return (
    <div
      ref={track}
      role="slider"
      tabIndex={0}
      aria-label={t("game.seek.label")}
      aria-valuemin={0}
      aria-valuemax={Math.round(limit)}
      aria-valuenow={Math.round(now)}
      aria-valuetext={t("game.seek.valueText", {
        current: Math.round(now),
        total: Math.round(limit),
      })}
      data-testid="seek-bar"
      onKeyDown={onKeyDown}
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        seekAt(e);
      }}
      onPointerMove={(e) => dragging.current && seekAt(e)}
      onPointerUp={() => (dragging.current = false)}
      onPointerCancel={() => (dragging.current = false)}
      className={cn(
        "relative h-7 w-full max-w-[320px] cursor-pointer touch-none rounded-full select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-music",
        className,
      )}
    >
      <span
        aria-hidden
        className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-rule"
      />
      <span
        aria-hidden
        data-testid="seek-open"
        className="absolute top-1/2 left-0 h-1.5 -translate-y-1/2 rounded-full bg-rule-strong"
        style={{ width: pct(limit) }}
      />
      <span
        aria-hidden
        className="absolute top-1/2 left-0 h-1.5 -translate-y-1/2 rounded-full bg-music"
        style={{ width: pct(now) }}
      />
      <span
        aria-hidden
        className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-music"
        style={{ left: pct(now) }}
      />
    </div>
  );
}
