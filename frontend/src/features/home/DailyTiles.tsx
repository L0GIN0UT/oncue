"use client";
/**
 * Плитки дейликов главной (макет Home): музыка — акцентная, кино — обычная. Плитка ведёт на
 * `/play/<mode_key>` (старт или возобновление сессии) либо на итоги, если дейлик сыгран.
 */
import { useTranslations } from "next-intl";
import type { DailyCard, Domain } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { formatClock, remainingUntil } from "@/features/daily/countdown-time";
import { useNow } from "@/features/daily/use-now";
import { Cover } from "@/shared/ui/cover";
import { Tile } from "@/shared/ui/tile";
import { useIcons } from "@/shared/ui/theme-provider";

export function dailyHref(card: DailyCard): string {
  return card.status === "finished" && card.session_id
    ? `/results/${card.session_id}`
    : `/play/${card.mode_key}`;
}

function ResetChip({ target }: { target: string }) {
  const t = useTranslations();
  const now = useNow();
  const clock = now ? formatClock(remainingUntil(new Date(target), now)) : "––:––:––";
  return (
    <span className="tabular rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold whitespace-nowrap text-ink-muted">
      {t("home.daily.resetIn", { clock })}
    </span>
  );
}

function StatusChip({ card }: { card: DailyCard | undefined }) {
  const t = useTranslations();
  if (!card)
    return (
      <span className="rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted">
        {t("home.daily.unavailable")}
      </span>
    );
  const finished = card.status === "finished";
  return (
    <span
      data-status={card.status}
      className={cn(
        "w-fit rounded-full px-[9px] py-[3px] text-xs font-semibold",
        finished ? "bg-exact-fill text-exact" : "bg-glow text-music",
      )}
    >
      {t(`home.daily.status.${card.status}`, { score: card.score ?? 0, max: card.max_score })}
    </span>
  );
}

export function DailyTile({ domain, card }: { domain: Domain; card: DailyCard | undefined }) {
  const t = useTranslations();
  const icons = useIcons();
  const Play = icons.play;
  const Icon = domain === "music" ? icons.music : icons.film;
  const music = domain === "music";
  return (
    <Tile
      accent={music}
      cap={t(`home.daily.${domain}.cap`)}
      aside={card ? <ResetChip target={card.next_reset_at} /> : undefined}
      title={music ? t(`home.daily.${domain}.title`) : undefined}
      subtitle={music ? t(`home.daily.${domain}.subtitle`) : undefined}
      href={card ? dailyHref(card) : undefined}
      data-testid={`home-daily-${domain}`}
      className="min-h-[196px]"
    >
      {music ? (
        <Icon
          aria-hidden
          className="pointer-events-none absolute top-10 -right-3 size-24 text-stage-raised"
          strokeWidth={1.5}
        />
      ) : (
        <div className="relative flex items-start gap-3">
          <Cover
            ratio="frame"
            alt={t("home.daily.frameAlt")}
            className="w-[116px] flex-none"
            placeholder={<Icon className="size-6" />}
          />
          <div className="flex min-w-0 flex-col gap-1.5">
            <span className="font-display text-xl leading-[1.1] font-extrabold tracking-[-0.02em]">
              {t("home.daily.film.title")}
            </span>
            <span className="text-[13px] leading-[1.45] text-ink-muted">
              {t("home.daily.film.subtitle")}
            </span>
          </div>
        </div>
      )}
      <div className="relative mt-auto flex items-center justify-between gap-3">
        <StatusChip card={card} />
        {music && card && card.status !== "finished" && (
          <span className="grid size-11 flex-none place-items-center rounded-full bg-music text-night">
            <Play aria-hidden className="ml-0.5 size-5" fill="currentColor" />
          </span>
        )}
      </div>
    </Tile>
  );
}
