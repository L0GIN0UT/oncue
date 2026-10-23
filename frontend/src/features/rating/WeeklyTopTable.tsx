"use client";
/** Топ недели ladder-а: прирост рейтинга с понедельника (ADR 0039); моя строка выделена. */
import { useFormatter, useTranslations } from "next-intl";
import type { WeeklyTop } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { signedDelta } from "./logic";
import { useLadderLabel } from "./labels";

export function WeeklyTopTable({
  top,
  limit,
  heading = "h3",
}: {
  top: WeeklyTop;
  /** Сколько строк показать (хаб — первые несколько). */
  limit?: number;
  heading?: "h2" | "h3";
}) {
  const t = useTranslations();
  const format = useFormatter();
  const ladderLabel = useLadderLabel();
  const Heading = heading;
  const items = limit ? top.items.slice(0, limit) : top.items;
  const meInList = top.me ? items.some((i) => i.user_id === top.me?.user_id) : false;
  const rows = top.me && !meInList ? [...items, top.me] : items;
  const since = format.dateTime(new Date(`${top.week_start}T00:00:00`), {
    day: "numeric",
    month: "long",
  });
  return (
    <div className="flex flex-col gap-2" data-testid={`weekly-${top.ladder}`}>
      <Heading className="font-display text-lg font-semibold">
        {t("rating.board.weekly")} · {ladderLabel(top.ladder)}
      </Heading>
      <p className="text-sm text-ink-muted">{t("rating.board.weeklyLead", { date: since })}</p>
      {rows.length === 0 ? (
        <p className="text-ink-muted">{t("rating.board.weeklyEmpty")}</p>
      ) : (
        <ol className="flex flex-col divide-y divide-rule rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule">
          {rows.map((r) => (
            <li
              key={r.user_id}
              className={cn(
                "flex items-center gap-4 px-4 py-3",
                r.user_id === top.me?.user_id && "bg-stage-raised",
              )}
              data-testid={`weekly-row-${r.place}`}
              value={r.place}
            >
              <span className="tabular w-8 font-display text-lg font-bold text-[var(--accent)]">
                {r.place}
              </span>
              <span className="min-w-0 flex-1 truncate" dir="auto">
                {r.nickname}
                {r.user_id === top.me?.user_id && (
                  <span className="text-sm text-ink-faint"> ({t("rating.board.you")})</span>
                )}
              </span>
              <span
                className={cn(
                  "tabular font-semibold",
                  r.delta > 0 ? "text-exact" : r.delta < 0 ? "text-miss" : "text-ink-muted",
                )}
              >
                {signedDelta(r.delta)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
