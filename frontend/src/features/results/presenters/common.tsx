"use client";
/** Общие части презентеров итога: строки раундов и рекорд режима. */
import { useTranslations } from "next-intl";
import { Trophy } from "lucide-react";
import type { ResultRecord, ResultRoundCard } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import type { PresenterProps } from "./types";

const OUTCOME_TONE: Record<ResultRoundCard["outcome"], string> = {
  exact: "bg-exact",
  partial: "bg-partial",
  miss: "bg-miss",
  gave_up: "bg-miss",
  void: "bg-skip",
};

/** Строка на каждый сыгранный раунд (у марафона и пары — столько, сколько успели сыграть). */
export function ResultRows({ summary, items }: PresenterProps) {
  const t = useTranslations();
  return (
    <ol className="flex flex-col gap-2" aria-label={t("results.roundsLabel")}>
      {summary.rounds.map((r) => {
        const item = r.media_item_id ? items.get(r.media_item_id) : undefined;
        return (
          <li
            key={r.index}
            data-testid="result-row"
            className="grid grid-cols-[auto_1fr_auto] items-center gap-x-4 gap-y-1 rounded-xl bg-stage px-4 py-3 ring-1 ring-rule"
          >
            <span aria-hidden className={cn("size-3 rounded-full", OUTCOME_TONE[r.outcome])} />
            <div className="min-w-0">
              <p className="truncate font-medium">{item?.title ?? t("results.unknownItem")}</p>
              <p className="truncate text-sm text-ink-muted">
                {t(`results.outcome.${r.outcome}`, { level: r.level ?? 0 })}
                {item
                  ? `, ${[item.year, ...item.persons.map((p) => p.name)].filter(Boolean).join(", ")}`
                  : ""}
              </p>
            </div>
            <span className="tabular font-display text-lg font-semibold">+{r.points}</span>
            {r.exact_pct !== null && r.exact_pct !== undefined && (
              <div className="col-span-3 col-start-1 mt-1 flex items-center gap-3 text-xs text-ink-muted sm:col-span-2 sm:col-start-2">
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-rule">
                  <span
                    className="block h-full rounded-full bg-[var(--accent)]"
                    style={{ width: `${Math.round(r.exact_pct)}%` }}
                  />
                </span>
                <span className="tabular">
                  {t("results.exactPct", { pct: Math.round(r.exact_pct) })}
                </span>
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Рекорд режима (только пользователи, режимы с `features.records`): лучший, новый, процентиль. */
export function RecordBadge({
  record,
  unit,
}: {
  record: ResultRecord | null | undefined;
  unit: "score" | "streak";
}) {
  const t = useTranslations();
  if (!record) return null;
  return (
    <p
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-full px-4 py-2 text-sm ring-1 ring-inset",
        record.is_new ? "bg-[var(--accent)] text-night ring-transparent" : "ring-rule",
      )}
      data-testid="record"
    >
      <Trophy aria-hidden className="size-4" />
      {record.is_new && <strong data-testid="record-new">{t("results.record.new")}</strong>}
      <span>{t(`results.record.best.${unit}`, { best: record.best })}</span>
      {record.percentile !== null && record.percentile !== undefined && (
        <span data-testid="record-percentile">
          {t("results.record.percentile", { pct: Math.round(record.percentile) })}
        </span>
      )}
    </p>
  );
}
