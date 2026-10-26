"use client";
/**
 * Строка попытки (54 px): целиком заливается цветом исхода. `empty` — пустая рамка, `current` —
 * текущая попытка с янтарной рамкой и свечением. Исход читает скринридер (`statusLabel`).
 */
import type { ReactNode } from "react";
import { Check, X } from "lucide-react";
import { cn } from "@/shared/lib/cn";

export type AttemptStatus = "exact" | "partial" | "miss" | "skip" | "current" | "empty";

const TONE: Record<AttemptStatus, string> = {
  exact: "border-exact-line bg-exact-fill",
  partial: "border-partial-line bg-partial-fill",
  miss: "border-miss-line bg-miss-fill",
  skip: "border-skip-line bg-skip-fill justify-center",
  current: "border-music bg-transparent shadow-[0_0_0_4px_var(--color-glow)]",
  empty: "border-rule bg-transparent",
};

type Props = {
  status: AttemptStatus;
  children?: ReactNode;
  /** Короткая подпись справа («исполнитель») у частичного совпадения. */
  hint?: ReactNode;
  /** Текст исхода для скринридера: «Угадал», «Мимо»… */
  statusLabel?: string;
  /** Появление строки (250 мс) — только для только что сделанной попытки. */
  animate?: boolean;
  className?: string;
};

export function AttemptRow({ status, children, hint, statusLabel, animate, className }: Props) {
  return (
    <div
      data-result={status}
      aria-hidden={status === "empty" ? true : undefined}
      className={cn(
        "flex h-[54px] items-center justify-between gap-3 rounded-[var(--radius-control)] border-[1.5px] px-[18px] text-base",
        TONE[status],
        animate && "animate-row-in",
        className,
      )}
    >
      {statusLabel && <span className="sr-only">{statusLabel}:</span>}
      <span
        className={cn(
          "min-w-0 flex-1 truncate",
          status === "skip" && "flex-none text-ink-muted",
          status === "current" && "text-ink-muted",
        )}
      >
        {children}
      </span>
      {status === "exact" && <Check className="size-[22px] shrink-0 text-exact" aria-hidden />}
      {status === "miss" && <X className="size-[22px] shrink-0 text-miss" aria-hidden />}
      {status === "partial" && hint != null && (
        <span className="shrink-0 text-[13px] font-semibold text-partial">{hint}</span>
      )}
    </div>
  );
}
