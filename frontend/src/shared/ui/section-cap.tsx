/** Капитель-подпись секции карточки: 11 px, разрядка, верхний регистр («ЛЕНТА ДЕЙЛИКА»). */
import type { ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

export function SectionCap({
  accent = false,
  className,
  ...props
}: ComponentProps<"span"> & { accent?: boolean }) {
  return (
    <span
      className={cn(
        "text-[11px] font-semibold tracking-[0.12em] uppercase",
        accent ? "text-music" : "text-ink-faint",
        className,
      )}
      {...props}
    />
  );
}
