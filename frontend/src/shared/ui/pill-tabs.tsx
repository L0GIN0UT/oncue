"use client";
/**
 * Вкладки-пилюли (макет «Соло»): tablist со стрелками, выбранная — светлая заливка. Содержимое
 * вкладки рисует вызывающий. Общий `Tabs` — с подчёркиванием.
 */
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export type PillTab<V extends string> = { value: V; label: ReactNode };

type Props<V extends string> = {
  tabs: readonly PillTab<V>[];
  value: V;
  onChange: (value: V) => void;
  "aria-label": string;
  /** Id панели, которой управляют вкладки (`aria-controls`). */
  panelId?: string;
};

const ARROW_STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export function PillTabs<V extends string>({
  tabs,
  value,
  onChange,
  panelId,
  "aria-label": label,
}: Props<V>) {
  const base = useId();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function move(e: KeyboardEvent, index: number) {
    const step = ARROW_STEP[e.key];
    if (!step) return;
    e.preventDefault();
    const next = (index + step + tabs.length) % tabs.length;
    onChange(tabs[next]!.value);
    refs.current[next]?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className="flex flex-wrap gap-2">
      {tabs.map((tab, i) => {
        const on = tab.value === value;
        return (
          <button
            key={tab.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={`${base}-${tab.value}`}
            type="button"
            role="tab"
            aria-selected={on}
            aria-controls={panelId}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(tab.value)}
            onKeyDown={(e) => move(e, i)}
            className={cn(
              "h-11 rounded-full border-[1.5px] px-4 text-sm font-medium transition-colors",
              on
                ? "border-ink bg-ink text-night"
                : "border-rule text-ink-muted hover:border-rule-strong hover:text-ink",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
