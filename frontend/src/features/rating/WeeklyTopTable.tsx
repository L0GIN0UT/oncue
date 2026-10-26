"use client";
/** Топ недели раздела: прирост рейтинга с понедельника (ADR 0039); моя строка выделена. */
import { useFormatter, useTranslations } from "next-intl";
import type { WeeklyTop } from "@/shared/api/types";
import { Avatar } from "@/shared/ui/avatar";
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
    <div className="flex flex-col gap-2.5" data-testid={`weekly-${top.ladder}`}>
      <Heading className="px-0.5 font-semibold">
        {t("rating.board.weekly")} · {ladderLabel(top.ladder)}
      </Heading>
      <p className="px-0.5 text-sm text-ink-muted">
        {t("rating.board.weeklyLead", { date: since })}
      </p>
      {rows.length === 0 ? (
        <p className="text-ink-muted">{t("rating.board.weeklyEmpty")}</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {rows.map((r) => {
            const mine = r.user_id === top.me?.user_id;
            return (
              <li
                key={r.user_id}
                className={cn(
                  "flex min-h-14 items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] px-4",
                  mine ? "border-music bg-stage" : "border-rule",
                )}
                data-testid={`weekly-row-${r.place}`}
                value={r.place}
              >
                <span
                  className={cn(
                    "tabular w-8 font-display font-extrabold",
                    mine ? "text-music" : "text-ink-muted",
                  )}
                >
                  {r.place}
                </span>
                <Avatar name={r.nickname} size="sm" className={mine ? "border-music" : undefined} />
                <span className="min-w-0 flex-1 truncate" dir="auto">
                  {r.nickname}
                  {mine && (
                    <span className="text-sm text-ink-muted"> ({t("rating.board.you")})</span>
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
            );
          })}
        </ol>
      )}
    </div>
  );
}
