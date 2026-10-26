"use client";
/**
 * Календарь активности: текущий месяц, дни серии дейлика янтарные, сегодня в рамке. Отметок по
 * дням бэкенд пока не отдаёт, поэтому закрашена серия (`calendar.ts`).
 */
import { useFormatter, useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { monthGrid, type DayState } from "./calendar";

const CELL: Record<DayState, string> = {
  played: "bg-music font-semibold text-night",
  today: "border-music font-semibold text-ink",
  missed: "bg-stage-raised text-ink-muted",
  future: "text-ink-dim",
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
    <section
      aria-label={t("progression.calendar.title")}
      data-testid="activity-calendar"
      className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-4"
    >
      <p className="text-center font-semibold capitalize">{month}</p>
      <div
        role="img"
        aria-label={t("progression.calendar.label", { month, streak })}
        className="grid grid-cols-7 gap-1"
      >
        {Array.from({ length: 7 }, (_, i) => (
          <span key={i} className="text-center text-[11px] text-ink-dim" aria-hidden>
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
              "grid aspect-square place-items-center rounded-lg border-[1.5px] border-transparent text-xs",
              CELL[d.state],
            )}
          >
            {d.day}
          </span>
        ))}
      </div>
    </section>
  );
}
