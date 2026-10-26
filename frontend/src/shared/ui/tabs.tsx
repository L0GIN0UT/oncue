"use client";
/** Вкладки с подчёркиванием: tablist со стрелками; содержимое вкладки рисует вызывающий (`TabPanel`). */
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export type TabItem<V extends string> = { value: V; label: ReactNode };

type Props<V extends string> = {
  tabs: readonly TabItem<V>[];
  value: V;
  onChange: (value: V) => void;
  "aria-label": string;
  /** Префикс id вкладок для `TabPanel`; по умолчанию уникальный. */
  idBase?: string;
  className?: string;
};

export function tabIds(idBase: string, value: string) {
  return { tab: `${idBase}-tab-${value}`, panel: `${idBase}-panel-${value}` };
}

const ARROW_STEP: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };

export function Tabs<V extends string>({
  tabs,
  value,
  onChange,
  idBase,
  className,
  "aria-label": label,
}: Props<V>) {
  const auto = useId();
  const base = idBase ?? auto;
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
    <div
      role="tablist"
      aria-label={label}
      className={cn("flex gap-[22px] overflow-x-auto border-b-[1.5px] border-rule", className)}
    >
      {tabs.map((t, i) => {
        const on = t.value === value;
        const ids = tabIds(base, t.value);
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            id={ids.tab}
            type="button"
            role="tab"
            aria-selected={on}
            aria-controls={ids.panel}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.value)}
            onKeyDown={(e) => move(e, i)}
            className={cn(
              "h-11 shrink-0 border-b-2 px-1 text-[15px] font-medium whitespace-nowrap transition-colors",
              on ? "border-music text-ink" : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  idBase,
  value,
  active,
  children,
}: {
  idBase: string;
  value: string;
  active: boolean;
  children: ReactNode;
}) {
  const ids = tabIds(idBase, value);
  return (
    <div role="tabpanel" id={ids.panel} aria-labelledby={ids.tab} hidden={!active}>
      {active ? children : null}
    </div>
  );
}
