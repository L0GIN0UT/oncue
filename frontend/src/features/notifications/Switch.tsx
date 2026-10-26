"use client";
/**
 * Переключатель по правилам системы: нативный чекбокс, нарисованный как пилюля с бегунком. Включён —
 * янтарная заливка; весь ряд с подписью нажимается (тап-цель не меньше 44 px).
 */
import type { ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

export function Switch({
  label,
  className,
  ...props
}: Omit<ComponentProps<"input">, "type"> & { label: string }) {
  return (
    <label
      className={cn(
        "flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm has-[:disabled]:cursor-not-allowed",
        className,
      )}
    >
      <span>{label}</span>
      <input
        type="checkbox"
        className="relative h-7 w-12 shrink-0 cursor-pointer appearance-none rounded-full border-[1.5px] border-rule bg-stage-raised transition-colors before:absolute before:top-0.5 before:left-0.5 before:size-5 before:rounded-full before:bg-ink-muted before:transition-transform checked:border-music checked:bg-music checked:before:translate-x-5 checked:before:bg-night disabled:cursor-not-allowed disabled:opacity-45 motion-reduce:transition-none motion-reduce:before:transition-none"
        {...props}
      />
    </label>
  );
}
