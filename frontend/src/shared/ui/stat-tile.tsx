/** Плитка показателя: крупное число Unbounded и подпись 13 px. */
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export function StatTile({
  value,
  label,
  accent = false,
  className,
}: {
  value: ReactNode;
  label: ReactNode;
  /** Число янтарём (одна плитка на экран). */
  accent?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-[var(--radius-control)] border-[1.5px] border-rule px-4 py-3.5",
        className,
      )}
    >
      <b
        className={cn(
          "tabular font-display text-2xl font-extrabold tracking-tight",
          accent && "text-music",
        )}
      >
        {value}
      </b>
      <span className="text-[13px] text-ink-muted">{label}</span>
    </div>
  );
}
