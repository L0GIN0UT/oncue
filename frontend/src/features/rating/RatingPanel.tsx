"use client";
/**
 * Мой рейтинг по ladder-ам: звание со ступенью («Бронза 2»), в калибровке — «осталось N матчей»
 * вместо звания, место, прогресс до следующего звания и строка сезона. Используется в хабе
 * онлайна и в профиле; данные — блоки `ratings` и `seasons` bff `/pages/online`.
 */
import { useTranslations } from "next-intl";
import { useNow } from "@/features/daily/use-now";
import { TierBadge, tierKeyOf } from "@/shared/ui/tier-badge";
import { useIcons } from "@/shared/ui/theme-provider";
import { daysLeft, pointsToNext } from "./logic";
import { useLadderLabel, useTierLabel, type RatingCard, type SeasonCard } from "./labels";

export function RatingPanel({
  ratings,
  seasons,
}: {
  /** `null` — блок недоступен. */
  ratings: RatingCard[] | null;
  seasons: SeasonCard[] | null;
}) {
  const t = useTranslations();
  if (ratings === null) {
    return (
      <p className="text-ink-muted" data-testid="ratings-unavailable">
        {t("rating.unavailable")}
      </p>
    );
  }
  if (!ratings.length) return <p className="text-ink-muted">{t("rating.mine.empty")}</p>;
  return (
    <ul className="grid gap-x-7 gap-y-5 sm:grid-cols-2" data-testid="ratings">
      {ratings.map((r) => (
        <li
          key={r.ladder}
          data-testid={`rating-${r.ladder}`}
          className="flex min-w-0 flex-col gap-2"
        >
          <RatingBody rating={r} season={seasons?.find((s) => s.ladder === r.ladder) ?? null} />
        </li>
      ))}
    </ul>
  );
}

function RatingBody({ rating, season }: { rating: RatingCard; season: SeasonCard | null }) {
  const t = useTranslations();
  const ladderLabel = useLadderLabel();
  const tierLabel = useTierLabel();
  const icons = useIcons();
  const Glyph = rating.ladder === "film" ? icons.film : icons.music;
  const left = pointsToNext(rating.rating, rating.next_tier_at);
  return (
    <>
      <h3 className="flex items-center gap-1.5 text-[13px] font-semibold text-ink-muted">
        <Glyph aria-hidden className="size-3.5" />
        {ladderLabel(rating.ladder)}
      </h3>
      {rating.calibrating || !rating.tier ? (
        <>
          <p className="font-display text-xl font-bold" data-testid="calibration">
            {t("rating.calibration", { count: rating.calibration_left })}
          </p>
          <p className="text-sm text-ink-muted">{t("rating.calibrationHint")}</p>
        </>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2.5">
            {rating.rating !== null && (
              <span className="tabular font-display text-[28px] leading-none font-extrabold">
                {rating.rating}
              </span>
            )}
            <span data-testid="tier">
              <TierBadge tier={tierKeyOf(rating.tier.family)}>{tierLabel(rating.tier)}</TierBadge>
            </span>
          </div>
          {rating.place != null && (
            <p className="tabular text-xs text-ink-muted">
              {t("rating.place", { place: rating.place })}
            </p>
          )}
          <p className="text-xs text-ink-muted" data-testid="next-tier">
            {left === null ? t("rating.topTier") : t("rating.toNext", { points: left })}
          </p>
        </>
      )}
      {season && <SeasonLine season={season} />}
    </>
  );
}

export function SeasonLine({ season }: { season: SeasonCard }) {
  const t = useTranslations();
  const now = useNow();
  const days = daysLeft(season.ends_at, (now ?? new Date()).getTime());
  const key =
    days === 0
      ? "rating.season.ended"
      : days === 1
        ? "rating.season.lastDay"
        : "rating.season.left";
  return (
    <p className="text-sm text-ink-faint" data-testid={`season-${season.ladder}`}>
      {t(key, { number: season.number, days })}
    </p>
  );
}
