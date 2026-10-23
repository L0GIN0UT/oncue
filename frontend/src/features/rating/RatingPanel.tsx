"use client";
/**
 * Мой рейтинг по ladder-ам: звание со ступенью («Бронза 2»), в калибровке — «осталось N матчей»
 * вместо звания, место, прогресс до следующего звания и строка сезона. Используется в хабе
 * онлайна и в профиле; данные — блоки `ratings` и `seasons` bff `/pages/online`.
 */
import { useTranslations } from "next-intl";
import { useNow } from "@/features/daily/use-now";
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
    <ul className="grid gap-3 sm:grid-cols-2" data-testid="ratings">
      {ratings.map((r) => (
        <li
          key={r.ladder}
          data-testid={`rating-${r.ladder}`}
          className="flex flex-col gap-2 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
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
  const left = pointsToNext(rating.rating, rating.next_tier_at);
  return (
    <>
      <h3 className="text-sm text-[var(--accent)]">{ladderLabel(rating.ladder)}</h3>
      {rating.calibrating || !rating.tier ? (
        <>
          <p className="font-display text-xl font-bold" data-testid="calibration">
            {t("rating.calibration", { count: rating.calibration_left })}
          </p>
          <p className="text-sm text-ink-muted">{t("rating.calibrationHint")}</p>
        </>
      ) : (
        <>
          <p className="font-display text-2xl font-bold" data-testid="tier">
            {tierLabel(rating.tier)}
          </p>
          <p className="tabular text-ink-muted">
            {rating.rating !== null && t("rating.points", { rating: rating.rating })}
            {rating.place != null && <> · {t("rating.place", { place: rating.place })}</>}
          </p>
          <p className="text-sm text-ink-muted" data-testid="next-tier">
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
