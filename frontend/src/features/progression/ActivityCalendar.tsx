"use client";
/**
 * Календарь активности: текущий месяц, дни серии дейлика янтарные, сегодня в рамке. Отметок по
 * дням бэкенд пока не отдаёт, поэтому закрашена серия (`calendar.ts`).
 */
import { useFormatter, useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { Tile } from "@/shared/ui/tile";
import { monthGrid, type DayState } from "./calendar";

const CELL: Record<DayState, string> = {
  played: "bg-music font-semibold text-night",
  today: "border-music font-semibold text-ink",
  missed: "bg-stage-raised text-ink-muted",
  future: "text-ink-faint",
};

// 1 января 2024 — понедельник: подписи дней недели берём у Intl, а не из словаря.
const MONDAY = new Date(2024, 0, 1);

export function ActivityCalendar({
  today,
  streak,
  playedToday,
}: {
  today: Date;
  streak: number;
  playedToday: boolean;
}) {
  const t = useTranslations();
  const f = useFormatter();
  const { leading, days } = monthGrid(today, streak, playedToday);
  const month = f.dateTime(today, { month: "long" });
  return (
    <Tile
      cap={<span className="capitalize">{month}</span>}
      aside={
        <span className="font-display text-base font-extrabold text-music">
          {t("progression.calendar.streak", { streak })}
        </span>
      }
      data-testid="activity-calendar"
    >
      <div
        role="img"
        aria-label={t("progression.calendar.label", { month, streak })}
        className="grid grid-cols-7 gap-1"
      >
        {Array.from({ length: 7 }, (_, i) => (
          <span key={i} className="text-center text-[11px] text-ink-faint" aria-hidden>
            {f.dateTime(new Date(MONDAY.getTime() + i * 86_400_000), { weekday: "short" })}
          </span>
        ))}
        {Array.from({ length: leading }, (_, i) => (
          <span key={`gap-${i}`} aria-hidden />
        ))}
        {days.map((d) => (
          <span
            key={d.day}
            data-state={d.state}
            aria-hidden
            className={cn(
              "grid h-[22px] place-items-center rounded-md border-[1.5px] border-transparent text-xs",
              CELL[d.state],
            )}
          >
            {d.day}
          </span>
        ))}
      </div>
    </Tile>
  );
}
