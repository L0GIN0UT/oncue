"use client";
/**
 * Страница сезонного события `/events/[key]` (этап 5d, ADR 0054; макет «Событие»): слева карта
 * с тропой и узлами-находками, справа «Задание» выбранной находки и «Награда». Фон карты —
 * картинка события (`map_url`), узлы стоят по координатам из данных (`x_pct`, `y_pct`); тропа —
 * сплошная до найденных, пунктир дальше, следующий узел пульсирует. Только пользователю (аноним —
 * вход, гость — регистрация). Тексты и награды приходят из определения события и показываются
 * как есть. Закрытые находки не кликабельны; узел — кнопка с понятной подписью.
 */
import { useState } from "react";
import Link from "next/link";
import { Star } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { EventFindCard, EventPageView } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { Avatar } from "@/shared/ui/avatar";
import { Button } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import { useViewer } from "@/features/auth/session";
import { useNow } from "@/features/daily/use-now";
import { HeaderLeading } from "@/features/shell/header-leading";
import { ProfileGate, gateOf } from "@/features/progression/ProfileView";
import { daysLeft } from "@/features/rating/logic";
import { playRoute, sortedFinds, trailPaths } from "./logic";
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
      <div className="flex flex-col items-start gap-3 pt-8" data-testid="event-error">
        <h1 className="font-display text-[28px] leading-tight font-extrabold tracking-[-0.03em]">
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

/**
 * Страница шире колонки 640: карта и задание рядом (макет 1280). Блок выходит из колонки
 * симметрично по центру и на узком экране сжимается до ширины окна с полями.
 */
const BREAKOUT = "relative left-1/2 w-[min(1280px,calc(100vw-2rem))] -translate-x-1/2";

function EventContent({ page }: { page: EventPageView }) {
  const t = useTranslations();
  const f = useFormatter();
  const now = useNow();
  const icons = useIcons();
  const Back = icons.back;
  const finds = sortedFinds(page.finds);
  const [selected, setSelected] = useState<string | null>(null);
  // Пока игрок не выбрал точку, показано задание следующей находки (или последняя найденная).
  const fallback = finds.find((x) => x.state === "open") ?? [...finds].reverse()[0] ?? null;
  const current = finds.find((x) => x.key === selected) ?? fallback;
  const ended = page.status !== "active";
  const left = now === null ? null : daysLeft(page.ends_at, now.getTime());
  return (
    <div className={cn(BREAKOUT, "flex flex-col gap-4 pt-4")} data-testid="event-page">
      <HeaderLeading>
        <Button asChild variant="ghost" size="sm">
          <Link href="/" aria-label={t("events.backLabel")}>
            <Back aria-hidden size={18} />
            {t("events.back")}
          </Link>
        </Button>
      </HeaderLeading>

      <header className="flex flex-wrap items-end justify-between gap-3 px-0.5">
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="text-[13px] text-ink-muted" data-testid="event-period">
            {ended
              ? t(page.status === "stopped" ? "events.status.stopped" : "events.status.finished")
              : [
                  t("events.banner.until", {
                    date: f.dateTime(new Date(page.ends_at), { day: "numeric", month: "long" }),
                  }),
                  left !== null && left > 0 ? t("events.daysLeft", { days: left }) : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
          </span>
          <h1
            dir="auto"
            className="font-display text-[32px] leading-none font-extrabold tracking-[-0.03em]"
          >
            {page.title}
          </h1>
          {page.description && (
            <p dir="auto" className="max-w-[60ch] text-sm text-ink-muted">
              {page.description}
            </p>
          )}
        </div>
        <Progress page={page} />
      </header>

      <section className="flex flex-wrap items-stretch gap-[18px]">
        <EventMap
          page={page}
          finds={finds}
          selected={current?.key ?? null}
          onSelect={setSelected}
        />
        <aside className="flex max-w-[420px] min-w-0 flex-[1_1_320px] flex-col gap-3.5">
          <span className="px-0.5 text-[13px] text-ink-muted">{t("events.task.caption")}</span>
          {current && <FindDetail key={current.key} find={current} animate={selected !== null} />}
          <Reward page={page} />
        </aside>
      </section>
    </div>
  );
}

/** Счётчик «7 / 12» крупно; для скринридера — прогресс-бар и текст «Найдено N из M». */
function Progress({ page }: { page: EventPageView }) {
  const t = useTranslations();
  return (
    <div className="flex items-baseline gap-1.5">
      <div
        role="progressbar"
        aria-label={t("events.progressLabel")}
        aria-valuemin={0}
        aria-valuemax={page.total}
        aria-valuenow={page.found}
        className="flex items-baseline gap-1.5"
      >
        <span
          aria-hidden
          className="tabular font-display text-[40px] leading-none font-extrabold tracking-[-0.03em]"
        >
          {page.found}
        </span>
        <span aria-hidden className="tabular font-display text-lg font-medium text-ink-muted">
          / {page.total}
        </span>
      </div>
      <span className="sr-only" data-testid="event-progress">
        {t("events.progress", { found: page.found, total: page.total })}
      </span>
    </div>
  );
}

const NODE_STYLE: Record<EventFindCard["state"], string> = {
  found: "border-0 bg-music text-night",
  open: "animate-pulse-ring border-2 border-music bg-stage text-music",
  locked: "border-[1.5px] border-rule-strong bg-stage text-ink-muted",
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
  onSelect: (key: string) => void;
}) {
  const t = useTranslations();
  const icons = useIcons();
  const Check = icons.correct;
  const Lock = icons.locked;
  const paths = trailPaths(finds);
  return (
    <div
      role="group"
      aria-label={t("events.map.title")}
      data-testid="event-map"
      className="relative min-w-0 flex-[999_1_560px] overflow-hidden rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage"
    >
      {page.map_url ? (
        // Карта — статика с S3/CDN; размеры заранее неизвестны, поэтому обычный <img>.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={page.map_url} alt="" className="block h-auto w-full" draggable={false} />
      ) : (
        <div
          className="aspect-[16/10] w-full bg-gradient-to-b from-stage to-stage-raised"
          aria-hidden
        />
      )}
      <svg
        aria-hidden
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        className="pointer-events-none absolute inset-0 size-full"
      >
        {paths.rest && (
          <path
            d={paths.rest}
            fill="none"
            className="stroke-rule-strong"
            strokeWidth={2}
            strokeDasharray="4 5"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        )}
        {paths.found && (
          <path
            d={paths.found}
            fill="none"
            className="stroke-music"
            strokeWidth={2.4}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>
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
            onClick={() => onSelect(find.key)}
            style={{ left: `${find.x_pct}%`, top: `${find.y_pct}%` }}
            className={cn(
              "absolute grid size-12 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full p-0",
              NODE_STYLE[find.state],
              locked && "cursor-not-allowed",
              selected === find.key && !locked && "shadow-[0_0_0_4px_var(--color-glow)]",
            )}
          >
            {find.state === "found" && <Check aria-hidden />}
            {find.state === "open" && <Star aria-hidden className="size-[22px]" />}
            {locked && <Lock aria-hidden size={18} />}
            <span
              aria-hidden
              className="absolute -right-1.5 -bottom-1.5 grid size-[22px] place-items-center rounded-full border-[1.5px] border-rule bg-night text-[11px] font-semibold text-ink"
            >
              {find.number}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** «Задание» выбранной находки: статус, название, запись дневника, что сделать, кнопка. */
function FindDetail({ find, animate }: { find: EventFindCard; animate: boolean }) {
  const t = useTranslations();
  const route = playRoute(find.play_page);
  const trig = find.trigger;
  const locked = find.state === "locked";
  const found = find.state === "found";
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-[18px]",
        animate && "animate-rise",
      )}
      data-testid="event-find-detail"
      role="region"
      aria-label={locked ? t("events.find.lockedTitle", { number: find.number }) : find.title}
    >
      <div className="flex items-start gap-3">
        <span
          className={cn(
            "grid size-11 shrink-0 place-items-center rounded-full border-[1.5px] font-display text-sm font-extrabold",
            found && "border-music bg-music text-night",
            find.state === "open" && "border-music bg-stage text-music",
            locked && "border-rule-strong bg-stage text-ink-muted",
          )}
        >
          {find.number}
        </span>
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-[13px] text-ink-muted">
            {t(`events.find.status.${find.state}`, { number: find.number })}
          </span>
          <span dir="auto" className="font-display text-[17px] leading-tight font-medium">
            {locked ? t("events.find.lockedTitle", { number: find.number }) : find.title}
          </span>
        </div>
      </div>
      <p
        dir="auto"
        className="border-l-2 border-rule pl-3.5 text-[15px] leading-relaxed text-ink-soft italic"
      >
        {locked ? t("events.diary.lockedText") : find.diary}
      </p>
      {!locked && (
        <p className="text-sm leading-normal text-ink-muted">
          {t("events.reward.find", { xp: find.reward.xp, verses: find.reward.verses })}
        </p>
      )}
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
      {!locked && route && (
        <Button asChild variant={found ? "secondary" : "primary"} className="w-full">
          <Link href={route} data-testid="event-play">
            {found ? t("events.find.playMore") : t("events.find.play")}
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
    <>
      <span className="px-0.5 text-[13px] text-ink-muted">{t("events.reward.caption")}</span>
      <section
        aria-labelledby="event-reward-title"
        data-testid="event-reward"
        data-completed={page.completed}
        className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-[18px]"
      >
        <div className="flex items-center gap-3">
          {reward.image_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={reward.image_url} alt="" className="size-14 rounded-full object-contain" />
          ) : (
            <Avatar name={reward.title} size="lg" />
          )}
          <div className="flex min-w-0 flex-col gap-0.5">
            <h2 id="event-reward-title" dir="auto" className="font-display text-base font-medium">
              {reward.title}
            </h2>
            <p className="text-[13px] text-ink-muted">
              {page.completed ? t("events.reward.received") : t("events.reward.hint")}
            </p>
          </div>
        </div>
        {page.completed && (
          <Button asChild variant="secondary" className="w-full">
            <Link href="/profile">{t("events.reward.wear")}</Link>
          </Button>
        )}
      </section>
    </>
  );
}
