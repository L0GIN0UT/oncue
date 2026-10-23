"use client";
/**
 * Полоса опыта: один расчёт процента для `aria-valuenow` и ширины заливки. `compact` — для
 * карточки на главной (текст — «до следующего уровня осталось N»), иначе — «X из Y опыта».
 */
import { useFormatter, useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { xpPercent } from "./logic";

export function XpBar({
  xpInLevel,
  xpToNext,
  level,
  compact = false,
}: {
  xpInLevel: number;
  xpToNext: number | null | undefined;
  level: number;
  compact?: boolean;
}) {
  const t = useTranslations();
  const f = useFormatter();
  const toNext = xpToNext ?? null;
  const percent = xpPercent(xpInLevel, toNext);
  const text =
    toNext === null
      ? t("progression.level.maxText")
      : compact
        ? t("progression.home.xpLeft", { left: f.number(toNext) })
        : t("progression.level.valueText", {
            current: f.number(xpInLevel),
            total: f.number(xpInLevel + toNext),
            next: level + 1,
          });
  return (
    <div className="flex flex-col gap-1.5">
      <div
        role="progressbar"
        aria-label={t("progression.level.barLabel")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-valuetext={text}
        data-testid="xp-bar"
        className={cn(
          "overflow-hidden rounded-full bg-night ring-1 ring-rule",
          compact ? "h-2" : "h-3",
        )}
      >
        <div
          className="h-full rounded-full bg-[var(--accent,var(--color-music))] transition-[width] duration-300 motion-reduce:transition-none"
          style={{ width: `${percent}%` }}
        />
      </div>
      {!compact && (
        <p className="tabular text-sm text-ink-muted" data-testid="xp-text">
          {text}
        </p>
      )}
    </div>
  );
}
