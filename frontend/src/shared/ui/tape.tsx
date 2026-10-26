"use client";
/**
 * Лента времени игрового экрана: деления по уровням, заливка открытого отрезка (400 мс) и бегунок-
 * треугольник с подписью над ним. `weights` — относительная длина уровней; деления стоят на их
 * накопленных границах (кроме последней). Серый маркер `opponent` — позиция соперника в дуэли.
 */
import { cn } from "@/shared/lib/cn";

export type TapeMarker = { at: number; label: string };

type Props = {
  weights: readonly number[];
  /** Доля заливки 0..1. */
  filled: number;
  /** Бегунок янтарём с подписью («4 с»); без него — только заливка. */
  marker?: TapeMarker | null;
  /** Метка соперника (серый бегунок). */
  opponent?: TapeMarker | null;
  "aria-label": string;
  className?: string;
};

const clamp = (n: number) => Math.min(1, Math.max(0, n));

/** Накопленные границы уровней в долях ленты, без последней (она — край ленты). */
export function tickPositions(weights: readonly number[]): number[] {
  const total = weights.reduce((a, b) => a + b, 0);
  if (total <= 0) return [];
  let sum = 0;
  return weights.slice(0, -1).map((w) => (sum += w) / total);
}

function Pointer({ marker, tone }: { marker: TapeMarker; tone: "me" | "opponent" }) {
  const color = tone === "me" ? "text-music" : "text-ink-muted";
  return (
    <div
      className="absolute top-0 -translate-x-1/2 transition-[left] duration-[400ms] ease-[var(--ease-reveal)]"
      style={{ left: `${clamp(marker.at) * 100}%` }}
      data-testid={tone === "me" ? "tape-marker" : "tape-opponent"}
    >
      <span className={cn("block text-center font-display text-[13px] font-medium", color)}>
        {marker.label}
      </span>
      <span
        aria-hidden
        className={cn(
          "mx-auto block h-0 w-0 border-x-[6px] border-t-[7px] border-x-transparent border-t-current",
          color,
        )}
      />
    </div>
  );
}

export function Tape({ weights, filled, marker, opponent, className, ...rest }: Props) {
  const value = clamp(filled);
  return (
    <div className={cn("relative pt-[26px]", className)}>
      {opponent && <Pointer marker={opponent} tone="opponent" />}
      {marker && <Pointer marker={marker} tone="me" />}
      <div
        role="progressbar"
        aria-label={rest["aria-label"]}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value * 100)}
        className="relative h-3.5 overflow-hidden rounded-[7px] border-[1.5px] border-rule bg-stage-raised"
      >
        <span
          className="absolute inset-y-0 left-0 bg-music transition-[width] duration-[400ms] ease-[var(--ease-reveal)]"
          style={{ width: `${value * 100}%` }}
        />
        {tickPositions(weights).map((p) => (
          <span
            key={p}
            aria-hidden
            className="absolute inset-y-0 w-0.5 bg-night"
            style={{ left: `${p * 100}%` }}
          />
        ))}
      </div>
    </div>
  );
}
