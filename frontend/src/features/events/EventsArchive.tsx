"use client";
/** Архив сезонных событий игрока в профиле (этап 5d): только чтение, ссылки на страницы событий. */
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import type { EventArchiveCard } from "@/shared/api/types";

export function EventsArchive({ events }: { events: EventArchiveCard[] }) {
  const t = useTranslations();
  const f = useFormatter();
  if (events.length === 0) return null;
  return (
    <section
      aria-labelledby="events-archive-title"
      data-testid="events-archive"
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule lg:col-span-2"
    >
      <h2 id="events-archive-title" className="font-display text-xl font-semibold">
        {t("events.archive.title")}
      </h2>
      <ul className="flex flex-col gap-2">
        {events.map((e) => (
          <li
            key={`${e.key}-${e.starts_at}`}
            data-testid={`events-archive-${e.key}`}
            className="flex flex-wrap items-baseline justify-between gap-2"
          >
            <Link
              href={`/events/${e.key}`}
              dir="auto"
              className="font-medium underline-offset-4 hover:underline"
            >
              {e.title}
            </Link>
            <span className="tabular text-sm text-ink-muted">
              {f.dateTime(new Date(e.starts_at), { month: "long", year: "numeric" })}
              {" · "}
              {t("events.archive.item", { found: e.found, total: e.total })}
              {e.completed && ` · ${t("events.archive.completed")}`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
