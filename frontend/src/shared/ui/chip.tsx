/** Чип-фильтр: пилюля 34 px; выбранный — светлая заливка. Кнопка с `aria-pressed`. */
import type { ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

export function Chip({
  selected = false,
  className,
  ...props
}: ComponentProps<"button"> & { selected?: boolean }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      className={cn(
        "inline-flex h-[34px] items-center gap-1.5 rounded-full border-[1.5px] px-3.5 text-sm transition-colors",
        selected
          ? "border-ink bg-ink font-medium text-night"
          : "border-rule text-ink-muted hover:border-rule-strong hover:text-ink",
        className,
      )}
      {...props}
    />
  );
}
