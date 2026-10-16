"use client";
/**
 * Метрика сравнения «больше/меньше» (рейтинг, популярность): число из снимка, без единиц —
 * какая метрика, решает конфиг режима, клиент её не знает.
 */
import { useFormatter, useTranslations } from "next-intl";

export function Metric({ value, testId }: { value: number; testId: string }) {
  const t = useTranslations();
  const format = useFormatter();
  return (
    <p className="flex items-baseline gap-2">
      <span className="text-sm text-ink-muted">{t("game.metric")}</span>
      <span className="tabular font-display text-3xl font-bold" data-testid={testId}>
        {format.number(value, { maximumFractionDigits: 1 })}
      </span>
    </p>
  );
}
