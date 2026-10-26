"use client";
/**
 * Плитка «Твой прогресс»: уровень с полосой опыта, серия и два ранга (музыка, кино). Гостю —
 * приглашение войти. Ранги берутся из хаба онлайна (`pages/online`), поэтому запрашиваются только
 * для пользователя.
 */
import { useFormatter, useTranslations } from "next-intl";
import type { HomeProgressCard, RatingCard } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { useOnlinePage } from "@/features/online/bff";
import { useTierLabel } from "@/features/rating/labels";
import { useIcons } from "@/shared/ui/theme-provider";
import { Tile } from "@/shared/ui/tile";
import { TierBadge, tierKeyOf } from "@/shared/ui/tier-badge";

/** Доля заполнения полосы уровня, 0–100; у высшего уровня — полная. */
export function levelShare(p: Pick<HomeProgressCard, "xp_in_level" | "xp_to_next">): number {
  if (p.xp_to_next == null) return 100;
  const span = p.xp_in_level + p.xp_to_next;
  return span > 0 ? Math.min(100, Math.round((p.xp_in_level / span) * 100)) : 0;
}

function Rank({ domain, card }: { domain: "music" | "film"; card: RatingCard | undefined }) {
  const t = useTranslations();
  const f = useFormatter();
  const icons = useIcons();
  const tierLabel = useTierLabel();
  const Icon = domain === "music" ? icons.music : icons.film;
  const rated = card && !card.calibrating && card.tier && card.rating != null;
  return (
    <div
      className="flex min-w-0 items-center gap-1.5"
      data-testid={`home-rank-${domain}`}
      title={t(`home.progress.${domain}Rank`)}
    >
      <Icon aria-hidden className="size-3.5 flex-none text-ink-muted" />
      <span className="sr-only">{t(`home.progress.${domain}Rank`)}</span>
      {rated ? (
        <>
          <span className="tabular font-display text-base font-extrabold">
            {f.number(card.rating!)}
          </span>
          <TierBadge tier={tierKeyOf(card.tier!.family)} className="truncate">
            {tierLabel(card.tier!)}
          </TierBadge>
        </>
      ) : (
        <span className="text-xs text-ink-muted">{t("home.progress.calibrating")}</span>
      )}
    </div>
  );
}

function Ranks() {
  const online = useOnlinePage();
  const ratings = blockData(online.data?.blocks?.ratings) ?? [];
  return (
    <div className="mt-auto grid grid-cols-2 gap-2">
      <Rank domain="music" card={ratings.find((r) => r.ladder === "music")} />
      <Rank domain="film" card={ratings.find((r) => r.ladder === "film")} />
    </div>
  );
}

export function ProgressTile({
  progress,
  isUser,
}: {
  progress: HomeProgressCard | null;
  isUser: boolean;
}) {
  const t = useTranslations();
  const icons = useIcons();
  if (!isUser)
    return (
      <Tile
        cap={t("home.progress.cap")}
        title={t("home.progress.guestTitle")}
        subtitle={t("home.progress.guestSubtitle")}
        href="/login"
        data-testid="home-progress"
        className="min-h-[196px]"
      >
        <span className="mt-auto w-fit rounded-full bg-stage-raised px-3 py-1 text-xs font-semibold">
          {t("home.progress.guestAction")}
        </span>
      </Tile>
    );
  const Streak = icons.event;
  return (
    <Tile
      cap={t("home.progress.cap")}
      aside={
        progress && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-music">
            <Streak aria-hidden className="size-3" />
            {t("home.progress.streak", { days: progress.best_streak })}
          </span>
        )
      }
      href="/profile"
      data-testid="home-progress"
      className="min-h-[196px]"
    >
      {progress ? (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-2 text-[13px]">
            <span className="font-semibold">
              {t("home.progress.level", { level: progress.level })}
            </span>
            <span className="text-[11px] text-ink-muted">
              {progress.xp_to_next == null
                ? t("home.progress.maxLevel")
                : t("home.progress.toNext", { xp: progress.xp_to_next, next: progress.level + 1 })}
            </span>
          </div>
          <div
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={levelShare(progress)}
            aria-label={t("home.progress.level", { level: progress.level })}
            className="h-1.5 overflow-hidden rounded-[3px] bg-stage-raised"
          >
            <div className="h-full bg-music" style={{ width: `${levelShare(progress)}%` }} />
          </div>
        </div>
      ) : (
        <p className="text-[13px] text-ink-muted">{t("home.progress.unavailable")}</p>
      )}
      <Ranks />
      {progress && (
        <span className="text-xs text-ink-muted">
          {progress.quests_open > 0
            ? t("home.progress.quests", { count: progress.quests_open })
            : t("home.progress.noQuests")}
        </span>
      )}
    </Tile>
  );
}
