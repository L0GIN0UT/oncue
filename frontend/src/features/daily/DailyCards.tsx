"use client";
/**
 * Карточки дейликов. SSR отдаёт публичный каркас, клиент догружает личный статус из bff
 * (`GET /api/web/v1/pages/home` с cookie, D2) и показывает отсчёт до `next_reset_at`.
 */
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { useCallback, useEffect, useRef } from "react";
import { api, unwrap } from "@/shared/api/client";
import type { DailyCard, HomePage } from "@/shared/api/types";
import { recoverSession } from "@/features/auth/session";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { Countdown } from "./Countdown";
import { nextMidnight } from "./countdown";

export const homeQueryKey = ["page", "home"] as const;

export function useHomePage(initial: HomePage | null) {
  return useQuery({
    queryKey: homeQueryKey,
    queryFn: async (): Promise<HomePage> => unwrap(await api().web.GET("/v1/pages/home")),
    initialData: initial ?? undefined,
  });
}

export function DailyBoard({ initial }: { initial: HomePage | null }) {
  const t = useTranslations();
  const client = useQueryClient();
  const { data, isPending } = useHomePage(initial);
  const block = data?.blocks.dailies;
  const cards = block?.status === "ok" ? (block.data ?? []) : [];
  const weeklyBlock = data?.blocks.weekly;
  const weekly = weeklyBlock?.status === "ok" ? (weeklyBlock.data ?? []) : [];
  const target = cards[0]?.next_reset_at ?? nextMidnight(new Date()).toISOString();
  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: homeQueryKey });
  }, [client]);

  // Личный блок дейликов bff отдаёт только субъекту; анониму или с просроченным access —
  // `unavailable` (game отвечает 401, а bff прячет его в блоке, и общий refresh на 401 не
  // срабатывает). Поэтому фронтенд сам восстанавливает сессию — refresh при живой cookie
  // `gv_csrf`, иначе гостевая — и перезапрашивает блок. Только один раз: недоступность из-за
  // сбоя апстрима так не лечится, и цикла запросов быть не должно.
  const recovered = useRef(false);
  useEffect(() => {
    const personal = [block?.status, weeklyBlock?.status];
    if (recovered.current || !personal.includes("unavailable")) return;
    recovered.current = true;
    void recoverSession()
      .then(refresh)
      .catch(() => undefined);
  }, [block?.status, weeklyBlock?.status, refresh]);

  return (
    <section aria-labelledby="dailies-title" className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h2 id="dailies-title" className="font-display text-xl font-semibold">
          {t("daily.title")}
        </h2>
        <Countdown target={target} onElapsed={refresh} />
      </div>
      {block?.status === "unavailable" || (!isPending && !block) ? (
        <p className="rounded-[var(--radius-ticket)] bg-stage p-6 text-ink-muted ring-1 ring-rule">
          {t("daily.unavailable")}
        </p>
      ) : (
        <ul className="grid gap-5 md:grid-cols-2">
          {cards.map((card) => (
            <li key={card.mode_key}>
              <DailyTicket card={card} />
            </li>
          ))}
        </ul>
      )}
      {weekly.length > 0 && <WeeklyBlock cards={weekly} />}
    </section>
  );
}

/** Наборы недели (сетка): тот же билет, отдельный блок со сроком смены темы. */
function WeeklyBlock({ cards }: { cards: DailyCard[] }) {
  const t = useTranslations();
  const format = useFormatter();
  const reset = cards[0]?.next_reset_at;
  return (
    <div className="flex flex-col gap-4" data-testid="weekly">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <h3 className="font-display text-lg font-semibold">{t("weekly.title")}</h3>
        {reset && (
          <p className="text-sm text-ink-muted">
            {t("weekly.nextReset", {
              date: format.dateTime(new Date(reset), { day: "numeric", month: "long" }),
            })}
          </p>
        )}
      </div>
      <ul className="grid gap-5 md:grid-cols-2">
        {cards.map((card) => (
          <li key={card.mode_key}>
            <DailyTicket card={card} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Билет дейлика: корешок с цветом домена, описание, состояние и главное действие. */
export function DailyTicket({ card }: { card: DailyCard }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const title = dt(card.title_key, card.mode_key);
  const finished = card.status === "finished";
  // Подача итога решает подписи: у сетки нет очков, у серии — не «из максимума».
  const scored = (card.presentation ?? "score") === "score";
  const pitch = scored
    ? t(`daily.pitch.${card.domain}`)
    : dt(card.title_key.replace(/\.title$/, ".description"), t(`daily.pitch.${card.domain}`));
  const href =
    finished && card.session_id ? `/results/${card.session_id}` : `/play/${card.mode_key}`;
  return (
    <article
      data-domain={card.domain}
      data-testid={`daily-${card.mode_key}`}
      className="relative flex h-full overflow-hidden rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule"
    >
      <div aria-hidden className="w-3 shrink-0 bg-[var(--accent)]" />
      <div className="flex flex-1 flex-col gap-5 p-6 sm:p-7">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-[var(--accent)]">{t(`domain.${card.domain}`)}</p>
            <h3 className="mt-1 font-display text-2xl leading-tight font-bold">{title}</h3>
          </div>
          <DomainGlyph domain={card.domain} />
        </div>
        <p className="max-w-[38ch] text-ink-muted">{pitch}</p>
        <div className="mt-auto flex flex-wrap items-center justify-between gap-4 border-t border-dashed border-rule pt-5">
          <p className={cn("text-sm", finished ? "text-ink" : "text-ink-muted")}>
            {finished
              ? scored
                ? t("daily.status.finished", { score: card.score ?? 0, max: card.max_score })
                : t("daily.status.finishedPlain")
              : scored
                ? t(`daily.status.${card.status}`)
                : t(`daily.statusPlain.${card.status}`)}
          </p>
          <Button asChild size="md" variant={finished ? "secondary" : "primary"}>
            <Link href={href}>{t(`daily.action.${card.status}`)}</Link>
          </Button>
        </div>
      </div>
    </article>
  );
}

/** Значок домена: шкала нарастающих отрезков (музыка) или кадр с перфорацией (кино). */
function DomainGlyph({ domain }: { domain: DailyCard["domain"] }) {
  if (domain === "music") {
    return (
      <span aria-hidden className="flex h-10 items-end gap-1">
        {[20, 32, 48, 70, 100].map((h) => (
          <span
            key={h}
            className="w-1.5 rounded-full bg-[var(--accent)]"
            style={{ height: `${h}%` }}
          />
        ))}
      </span>
    );
  }
  return (
    <span
      aria-hidden
      className="grid h-10 w-14 grid-rows-[auto_1fr_auto] rounded-md bg-[var(--accent)] p-1"
    >
      <span className="flex justify-between">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="size-1 rounded-[1px] bg-stage" />
        ))}
      </span>
      <span className="my-0.5 rounded-sm bg-stage/80" />
      <span className="flex justify-between">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="size-1 rounded-[1px] bg-stage" />
        ))}
      </span>
    </span>
  );
}
