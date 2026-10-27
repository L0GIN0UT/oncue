"use client";
/** Общие части презентеров итога: строки раундов и рекорд режима. */
import { useFormatter, useTranslations } from "next-intl";
import { Film, Music, Trophy } from "lucide-react";
import type { ResultRecord, ResultRoundCard } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Cover } from "@/shared/ui/cover";
import { levelSeconds } from "@/features/game/levels";
import { SaveButton } from "@/features/library/SaveButton";
import type { PresenterProps } from "./types";

/** Цвет очков раунда: зелёные у угаданного и частичного, красные у нуля. */
const POINTS_TONE: Record<ResultRoundCard["outcome"], string> = {
  exact: "text-exact",
  partial: "text-partial",
  miss: "text-miss",
  gave_up: "text-miss",
  void: "text-skip",
};

/**
 * Строка на каждый сыгранный раунд (у марафона и пары — столько, сколько успели сыграть): обложка
 * 48 px (плашка, пока бэкенд не отдаёт картинки), название и исполнитель, «открыто N с» либо
 * исход словами, очки. У раунда с единицей — «Сохранить» в медиатеку (этап 3b, D5; источник
 * `results`).
 */
export function ResultRows({ summary, items, domain, levels }: PresenterProps) {
  const t = useTranslations();
  const format = useFormatter();
  const Icon = domain === "film" ? Film : Music;
  return (
    <ol className="flex flex-col gap-2.5" aria-label={t("results.roundsLabel")}>
      {summary.rounds.map((r) => {
        const item = r.media_item_id ? items.get(r.media_item_id) : undefined;
        const seconds = r.level !== null ? levelSeconds(levels, r.level) : null;
        const persons = item?.persons.map((p) => p.name).join(", ");
        return (
          <li
            key={r.index}
            data-testid="result-row"
            className="flex flex-wrap items-center gap-x-3.5 gap-y-2 rounded-[14px] border-[1.5px] border-rule bg-stage px-3.5 py-3"
          >
            <Cover
              ratio={domain === "film" ? "poster" : "square"}
              src={null}
              placeholder={<Icon className="size-5" aria-hidden />}
              className="w-12 flex-none rounded-[10px]"
            />
            <div className="min-w-0 flex-1 basis-40">
              <p dir="auto" className="truncate text-base font-semibold">
                {item?.title ?? t("results.unknownItem")}
              </p>
              <p className="truncate text-[13px] text-ink-muted">
                {[persons, item?.year].filter(Boolean).join(" · ")}
                {r.exact_pct !== null && r.exact_pct !== undefined && (
                  <>
                    {persons || item?.year ? " · " : ""}
                    {t("results.exactPct", { pct: Math.round(r.exact_pct) })}
                  </>
                )}
              </p>
            </div>
            <span className="text-[13px] text-ink-muted">
              {seconds !== null && r.outcome !== "void"
                ? t("results.opened", {
                    seconds: format.number(seconds, { maximumFractionDigits: 1 }),
                  })
                : t(`results.outcome.${r.outcome}`, { level: r.level ?? 0 })}
            </span>
            <span
              className={cn(
                "tabular min-w-16 text-right font-display text-lg font-extrabold",
                POINTS_TONE[r.outcome],
              )}
            >
              {r.points > 0 ? `+${r.points}` : r.points}
            </span>
            {r.media_item_id && (
              <SaveButton mediaItemId={r.media_item_id} domain={item?.domain} source="results" />
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
        "flex flex-wrap items-center justify-center gap-x-3 gap-y-1 rounded-[var(--radius-control)] border-[1.5px] px-4 py-2 text-sm",
        record.is_new ? "border-music bg-music text-night" : "border-rule",
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
