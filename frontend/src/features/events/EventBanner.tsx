"use client";
/**
 * Баннер активного сезонного события (этап 5d): компактная ссылка в шапке и карточка на главной.
 * Данные — блок `event` bff (оболочка и главная); `found`/`total` есть только у вошедшего игрока.
 * Названия приходят из определения события и показываются как есть.
 */
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import type { EventBannerCard, HomePage } from "@/shared/api/types";
import { useIcons } from "@/shared/ui/theme-provider";
import { useHomePage } from "@/features/daily/DailyCards";
import { useShellEvent } from "./use-event";

function useProgressText(event: EventBannerCard): string | null {
  const t = useTranslations();
  return event.found != null && event.total != null
    ? t("events.progress", { found: event.found, total: event.total })
    : null;
}

/** Ссылка в шапке: название события и прогресс. */
export function HeaderEventLink() {
  const event = useShellEvent();
  if (!event) return null;
  return <HeaderEventLinkView event={event} />;
}

function HeaderEventLinkView({ event }: { event: EventBannerCard }) {
  const t = useTranslations();
  const icons = useIcons();
  const progress = useProgressText(event);
  const Icon = icons.event;
  return (
    <Link
      href={`/events/${event.key}`}
      data-testid="nav-event"
      aria-label={
        progress
          ? t("events.banner.linkLabelProgress", { title: event.title, progress })
          : t("events.banner.linkLabel", { title: event.title })
      }
      className="inline-flex items-center gap-1.5 rounded-md px-3 py-2 font-medium text-[var(--color-film)] hover:underline"
    >
      <Icon aria-hidden className="size-4" />
      <span dir="auto" className="max-w-[10rem] truncate">
        {event.title}
      </span>
      {progress && <span className="tabular text-ink-muted">{progress}</span>}
    </Link>
  );
}

/** Карточка события на главной; пока главная не загрузилась — блок `event` из SSR. */
export function HomeEventBanner({ initial }: { initial: HomePage | null }) {
  const t = useTranslations();
  const f = useFormatter();
  const { data } = useHomePage(initial);
  const event = data?.blocks.event ?? null;
  if (!event) return null;
  return (
    <section
      aria-labelledby="home-event-title"
      data-testid="home-event"
      className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-[var(--color-film)] sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex flex-col gap-1">
        <h2 id="home-event-title" dir="auto" className="font-display text-lg font-semibold">
          {event.title}
        </h2>
        <p className="text-sm text-ink-muted">
          {t("events.banner.until", {
            date: f.dateTime(new Date(event.ends_at), { day: "numeric", month: "long" }),
          })}
          {event.found != null && event.total != null && (
            <span data-testid="home-event-progress">
              {" · "}
              {t("events.progress", { found: event.found, total: event.total })}
            </span>
          )}
        </p>
      </div>
      <Link
        href={`/events/${event.key}`}
        data-testid="home-event-open"
        className="self-start rounded-[var(--radius-control)] px-4 py-2.5 font-medium text-ink ring-1 ring-rule ring-inset hover:bg-stage-raised sm:self-auto"
      >
        {t("events.banner.open")}
      </Link>
    </section>
  );
}
