"use client";
/** Переключатель-пилюля («Музыка / Кино»): radiogroup со стрелками, выбранный — светлая заливка. */
import { useRef, type KeyboardEvent } from "react";
import { cn } from "@/shared/lib/cn";

export type SegmentedOption<V extends string> = { value: V; label: string };

type Props<V extends string> = {
  options: readonly SegmentedOption<V>[];
  value: V;
  onChange: (value: V) => void;
  "aria-label": string;
  className?: string;
};

const ARROW_STEP: Record<string, number> = {
  ArrowRight: 1,
  ArrowDown: 1,
  ArrowLeft: -1,
  ArrowUp: -1,
};

export function Segmented<V extends string>({
  options,
  value,
  onChange,
  className,
  "aria-label": label,
}: Props<V>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function move(e: KeyboardEvent, index: number) {
    const step = ARROW_STEP[e.key];
    if (!step) return;
    e.preventDefault();
    const next = (index + step + options.length) % options.length;
    onChange(options[next]!.value);
    refs.current[next]?.focus();
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        "inline-flex gap-0.5 rounded-full border-[1.5px] border-rule bg-stage p-[3px]",
        className,
      )}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => move(e, i)}
            className={cn(
              "inline-flex h-10 items-center rounded-full px-5 text-sm transition-colors",
              on ? "bg-ink font-semibold text-night" : "text-ink-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
