"use client";
/**
 * Страница сезонного события `/events/[key]` (этап 5d, ADR 0054): карта с точками находок,
 * дневник, прогресс «N из M», кнопка «играть» и награда. Только пользователю (аноним — вход,
 * гость — регистрация). Тексты и награды приходят из определения события и показываются как есть.
 * Закрытые находки не кликабельны; точка — кнопка с понятной подписью (клавиатура, скринридер).
 */
import { useState } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { EventFindCard, EventPageView } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { useViewer } from "@/features/auth/session";
import { ProfileGate, gateOf } from "@/features/progression/ProfileView";
import { playRoute, progressPct, sortedFinds } from "./logic";
import { useEventPage } from "./use-event";

export function EventView({ eventKey }: { eventKey: string }) {
  const t = useTranslations();
  const viewer = useViewer();
  const query = useEventPage(eventKey);

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;
  if (query.isError && !query.data) {
    const err = isApiError(query.error) ? query.error : null;
    const gate = gateOf(err?.status ?? null, err?.code ?? null);
    if (gate) return <ProfileGate variant={gate} />;
    const notFound = err?.status === 404;
    return (
      <div className="flex max-w-xl flex-col items-start gap-3 pt-12" data-testid="event-error">
        <h1 className="font-display text-2xl font-bold">
          {notFound ? t("events.notFound.title") : t("events.unavailable")}
        </h1>
        {notFound && <p className="text-ink-muted">{t("events.notFound.text")}</p>}
        <Button asChild variant="secondary">
          <Link href="/">{t("common.home")}</Link>
        </Button>
      </div>
    );
  }
  if (!query.data)
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  return <EventContent page={query.data} />;
}

function EventContent({ page }: { page: EventPageView }) {
  const t = useTranslations();
  const f = useFormatter();
  const finds = sortedFinds(page.finds);
  const [selected, setSelected] = useState<string | null>(null);
  const current = finds.find((x) => x.key === selected) ?? null;
  const ended = page.status !== "active";
  return (
    <div className="flex flex-col gap-8 pt-6 sm:pt-10" data-testid="event-page">
      <header className="flex flex-col gap-2">
        <h1 dir="auto" className="font-display text-3xl font-bold">
          {page.title}
        </h1>
        <p dir="auto" className="max-w-[60ch] text-ink-muted">
          {page.description}
        </p>
        <p className="text-sm text-ink-muted" data-testid="event-period">
          {ended
            ? t(page.status === "stopped" ? "events.status.stopped" : "events.status.finished")
            : t("events.banner.until", {
                date: f.dateTime(new Date(page.ends_at), { day: "numeric", month: "long" }),
              })}
        </p>
      </header>

      <Progress page={page} />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <EventMap page={page} finds={finds} selected={selected} onSelect={setSelected} />
        <Diary finds={finds} current={current} />
      </div>

      <Reward page={page} />
    </div>
  );
}

function Progress({ page }: { page: EventPageView }) {
  const t = useTranslations();
  const pct = progressPct(page.found, page.total);
  return (
    <section aria-labelledby="event-progress-title" className="flex flex-col gap-2">
      <h2 id="event-progress-title" className="font-display text-xl font-semibold">
        <span data-testid="event-progress">
          {t("events.progress", { found: page.found, total: page.total })}
        </span>
      </h2>
      <div
        role="progressbar"
        aria-labelledby="event-progress-title"
        aria-valuemin={0}
        aria-valuemax={page.total}
        aria-valuenow={page.found}
        className="h-2 overflow-hidden rounded-full bg-stage-raised"
      >
        <div className="h-full bg-[var(--color-film)]" style={{ width: `${pct}%` }} />
      </div>
    </section>
  );
}

const STATE_STYLE: Record<EventFindCard["state"], string> = {
  found: "bg-[var(--color-film)] text-night",
  open: "bg-stage-raised text-ink ring-2 ring-[var(--color-music)]",
  locked: "bg-stage text-ink-faint ring-1 ring-rule opacity-70",
};

function EventMap({
  page,
  finds,
  selected,
  onSelect,
}: {
  page: EventPageView;
  finds: EventFindCard[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const t = useTranslations();
  return (
    <section aria-label={t("events.map.title")} className="flex flex-col gap-2">
      <div
        data-testid="event-map"
        className="relative w-full overflow-hidden rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule"
      >
        {page.map_url ? (
          // Карта — статика с S3/CDN; размеры заранее неизвестны, поэтому обычный <img>.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={page.map_url} alt="" className="block h-auto w-full" draggable={false} />
        ) : (
          <div className="aspect-[4/3] w-full" aria-hidden />
        )}
        {finds.map((find) => {
          const locked = find.state === "locked";
          const state = t(`events.state.${find.state}`);
          return (
            <button
              key={find.key}
              type="button"
              disabled={locked}
              aria-pressed={locked ? undefined : selected === find.key}
              aria-label={t("events.map.point", { number: find.number, title: find.title, state })}
              data-testid={`event-point-${find.key}`}
              data-state={find.state}
              onClick={() => onSelect(selected === find.key ? null : find.key)}
              style={{ left: `${find.x_pct}%`, top: `${find.y_pct}%` }}
              className={cn(
                "absolute grid size-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full text-sm font-bold",
                STATE_STYLE[find.state],
                locked ? "cursor-not-allowed" : "hover:brightness-110",
                selected === find.key && "outline-offset-2",
              )}
            >
              <span aria-hidden>{find.number}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function Diary({ finds, current }: { finds: EventFindCard[]; current: EventFindCard | null }) {
  const t = useTranslations();
  return (
    <section
      aria-labelledby="event-diary-title"
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
    >
      <h2 id="event-diary-title" className="font-display text-xl font-semibold">
        {t("events.diary.title")}
      </h2>
      {current && <FindDetail find={current} />}
      <ol className="flex flex-col gap-3" data-testid="event-diary">
        {finds.map((find) => (
          <li
            key={find.key}
            data-testid={`event-diary-${find.key}`}
            data-state={find.state}
            className="flex flex-col gap-1"
          >
            <p className="font-medium" dir="auto">
              {find.number}. {find.state === "locked" ? t("events.diary.locked") : find.title}
              <span className="sr-only"> — {t(`events.state.${find.state}`)}</span>
            </p>
            {find.state !== "locked" && (
              <p className="text-sm text-ink-muted" dir="auto">
                {find.diary}
              </p>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}

function FindDetail({ find }: { find: EventFindCard }) {
  const t = useTranslations();
  const route = playRoute(find.play_page);
  const trig = find.trigger;
  return (
    <div
      className="flex flex-col gap-2 rounded-[var(--radius-control)] bg-stage-raised p-4"
      data-testid="event-find-detail"
      role="region"
      aria-label={find.title}
    >
      <p className="font-medium" dir="auto">
        {find.title}
      </p>
      <p className="text-sm text-ink-muted" dir="auto">
        {find.diary}
      </p>
      <p className="text-sm text-ink-muted">
        {t("events.reward.find", { xp: find.reward.xp, verses: find.reward.verses })}
      </p>
      {find.state === "open" &&
        trig.type === "server" &&
        trig.progress != null &&
        trig.target != null && (
          <p className="tabular text-sm" data-testid="event-find-progress">
            {t("events.find.progress", { progress: trig.progress, target: trig.target })}
          </p>
        )}
      {find.state === "open" && trig.type === "ui_spot" && (
        <p className="text-sm text-ink-muted">{t("events.find.spotHint")}</p>
      )}
      {find.state === "open" && route && (
        <Button asChild size="sm" className="self-start">
          <Link href={route} data-testid="event-play">
            {t("events.find.play")}
          </Link>
        </Button>
      )}
    </div>
  );
}

function Reward({ page }: { page: EventPageView }) {
  const t = useTranslations();
  const reward = page.reward_item;
  if (!reward) return null;
  return (
    <section
      aria-labelledby="event-reward-title"
      data-testid="event-reward"
      data-completed={page.completed}
      className="flex items-center gap-4 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={reward.image_url} alt="" className="size-16 rounded-md object-contain" />
      <div className="flex flex-col gap-1">
        <h2 id="event-reward-title" className="font-display text-lg font-semibold">
          {t("events.reward.title")}
        </h2>
        <p dir="auto">{reward.title}</p>
        <p className="text-sm text-ink-muted">
          {page.completed ? t("events.reward.received") : t("events.reward.hint")}
        </p>
      </div>
    </section>
  );
}
