/**
 * Плотная строка внутри плитки (`.lr` макета, 40 px): слева крупная метка Unbounded
 * (число, ранг), затем текст, справа дополнение. Не путать с `Row` (52–68 px, список экрана).
 */
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export function ListRow({
  lead,
  trailing,
  accentLead = false,
  className,
  children,
  "data-testid": testId,
}: {
  lead?: ReactNode;
  trailing?: ReactNode;
  /** Метку слева янтарём. */
  accentLead?: boolean;
  className?: string;
  children?: ReactNode;
  "data-testid"?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-10 items-center gap-2.5 rounded-xl bg-stage-raised px-2.5 text-[13px]",
        className,
      )}
      data-testid={testId}
    >
      {lead != null && (
        <b
          className={cn(
            "tabular min-w-[34px] font-display text-[15px] font-extrabold",
            accentLead && "text-music",
          )}
        >
          {lead}
        </b>
      )}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing != null && <span className="shrink-0 text-ink-muted">{trailing}</span>}
    </div>
  );
}
