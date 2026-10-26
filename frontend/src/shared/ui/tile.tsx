/**
 * Карточка-плитка хаба (`.tile` макета): капитель слева и необязательное дополнение справа,
 * заголовок Unbounded, подпись и слот содержимого. Со ссылкой (`href`) целиком кликабельна;
 * `accent` — янтарная обводка со свечением (одна плитка на экран).
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";
import { SectionCap } from "./section-cap";

type Props = {
  /** Капитель-подпись секции. */
  cap?: ReactNode;
  /** Дополнение справа от капители (чип, счётчик). */
  aside?: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Янтарная обводка со свечением. */
  accent?: boolean;
  href?: string;
  className?: string;
  children?: ReactNode;
  "data-testid"?: string;
};

export function Tile({
  cap,
  aside,
  title,
  subtitle,
  accent = false,
  href,
  className,
  children,
  "data-testid": testId,
}: Props) {
  const classes = cn(
    "relative flex min-w-0 flex-col gap-2.5 overflow-hidden rounded-[var(--radius-ticket)] border-[1.5px] bg-stage px-[18px] py-4 transition-[border-color,transform]",
    accent ? "border-music shadow-[0_0_0_4px_var(--color-glow)]" : "border-rule",
    href && !accent && "hover:border-rule-strong",
    href && "hover:-translate-y-0.5",
    className,
  );
  const inner = (
    <>
      {(cap != null || aside != null) && (
        <div className="relative flex items-center justify-between gap-3">
          {cap != null && <SectionCap accent={accent}>{cap}</SectionCap>}
          {aside}
        </div>
      )}
      {title != null && (
        <span className="relative font-display text-xl leading-[1.1] font-extrabold tracking-[-0.02em]">
          {title}
        </span>
      )}
      {subtitle != null && (
        <span className="relative text-[13px] leading-[1.45] text-ink-muted">{subtitle}</span>
      )}
      {children}
    </>
  );
  if (href)
    return (
      <Link href={href} className={classes} data-testid={testId}>
        {inner}
      </Link>
    );
  return (
    <div className={classes} data-testid={testId}>
      {inner}
    </div>
  );
}
