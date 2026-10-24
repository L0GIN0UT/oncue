"use client";
/** Вкладки по WAI-ARIA: roving tabindex, стрелки, Home/End. Панель — снаружи, связь по `aria-controls`. */
import { useId, useRef, type KeyboardEvent } from "react";
import { cn } from "@/shared/lib/cn";

export type TabItem = { id: string; label: string; testId: string };

type Props = {
  tabs: TabItem[];
  active: string;
  onChange: (id: string) => void;
  label: string;
  panelId: string;
};

export function Tabs({ tabs, active, onChange, label, panelId }: Props) {
  const base = useId();
  const refs = useRef(new Map<string, HTMLButtonElement>());

  const move = (to: number) => {
    const next = tabs[(to + tabs.length) % tabs.length];
    if (!next) return;
    onChange(next.id);
    refs.current.get(next.id)?.focus();
  };

  const onKeyDown = (e: KeyboardEvent, index: number) => {
    if (e.key === "ArrowRight") move(index + 1);
    else if (e.key === "ArrowLeft") move(index - 1);
    else if (e.key === "Home") move(0);
    else if (e.key === "End") move(tabs.length - 1);
    else return;
    e.preventDefault();
  };

  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto pb-1">
      {tabs.map((tab, i) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(el) => {
              if (el) refs.current.set(tab.id, el);
              else refs.current.delete(tab.id);
            }}
            type="button"
            role="tab"
            id={`${base}-${tab.id}`}
            aria-selected={selected}
            aria-controls={panelId}
            tabIndex={selected ? 0 : -1}
            data-testid={tab.testId}
            onClick={() => onChange(tab.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "shrink-0 rounded-[var(--radius-control)] px-4 py-2 text-sm font-medium whitespace-nowrap",
              selected
                ? "bg-ink text-night"
                : "text-ink-muted ring-1 ring-rule ring-inset hover:bg-stage-raised hover:text-ink",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
