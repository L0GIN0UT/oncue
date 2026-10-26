/**
 * Строка списка (52–68 px, граница 1,5 px, скругление 14): слева содержимое, справа дополнение.
 * Со ссылкой (`href`) или кнопкой (`onClick`) целиком интерактивна; иначе — статичный блок.
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

type Props = {
  children: ReactNode;
  trailing?: ReactNode;
  href?: string;
  onClick?: () => void;
  selected?: boolean;
  /** Высота: `md` 52, `lg` 68 (две строки текста). */
  size?: "md" | "lg";
  className?: string;
  "data-testid"?: string;
};

export function Row({
  children,
  trailing,
  href,
  onClick,
  selected = false,
  size = "md",
  className,
  "data-testid": testId,
}: Props) {
  const classes = cn(
    "flex w-full items-center justify-between gap-3 rounded-[var(--radius-control)] border-[1.5px] px-[18px] text-left text-base",
    size === "md" ? "min-h-[52px]" : "min-h-[68px] py-2",
    selected ? "border-music bg-stage-raised" : "border-rule bg-stage",
    (href || onClick) && "transition-colors hover:border-rule-strong",
    className,
  );
  const inner = (
    <>
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {trailing != null && <span className="shrink-0 text-sm text-ink-muted">{trailing}</span>}
    </>
  );
  if (href)
    return (
      <Link href={href} className={classes} data-testid={testId}>
        {inner}
      </Link>
    );
  if (onClick)
    return (
      <button type="button" onClick={onClick} className={classes} data-testid={testId}>
        {inner}
      </button>
    );
  return (
    <div className={classes} data-testid={testId}>
      {inner}
    </div>
  );
}
