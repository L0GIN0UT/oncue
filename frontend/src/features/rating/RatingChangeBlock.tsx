"use client";
/**
 * Изменение рейтинга в итоге рейтингового матча (D4 плана 4b): опрос `GET /matches/{id}/changes`
 * до 10 с. Отдельный блок: любой сбой или таймаут даёт спокойную строку, итог матча не ломается.
 * В калибровке числа скрыты — «осталось N матчей».
 */
import { useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { useMatchChanges, type MatchChange } from "./api";
import { signedDelta } from "./logic";
import { useLadderLabel, useTierLabel } from "./labels";

export function RatingChangeBlock({ matchId, myId }: { matchId: string; myId: string }) {
  const t = useTranslations();
  const query = useMatchChanges(matchId, true);
  const mine = query.data?.items.find((c) => c.user_id === myId) ?? null;

  return (
    <section
      aria-labelledby="summary-rating"
      className="flex flex-col gap-3"
      data-testid="rating-change"
    >
      <h2 id="summary-rating" className="font-semibold">
        {t("rating.change.title")}
      </h2>
      {query.isPending ? (
        <p className="text-ink-muted" role="status" data-testid="rating-change-loading">
          {t("rating.change.loading")}
        </p>
      ) : mine ? (
        <ChangeCard change={mine} />
      ) : (
        <p className="text-ink-muted" data-testid="rating-change-later">
          {t("rating.change.later")}
        </p>
      )}
    </section>
  );
}

function ChangeCard({ change }: { change: MatchChange }) {
  const t = useTranslations();
  const ladderLabel = useLadderLabel();
  const tierLabel = useTierLabel();
  const tierChanged = change.tier && change.old_tier && change.tier.key !== change.old_tier.key;
  // Числа есть не всегда (калибровка): без любого из них показываем «осталось N матчей».
  const { delta, old, new: next } = change;
  const numbers =
    !change.calibrating && delta !== null && old !== null && next !== null
      ? { delta, old, next }
      : null;
  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage p-5">
      <p className="text-sm text-[var(--accent)]">
        {t("rating.change.ladder", { ladder: ladderLabel(change.ladder) })}
      </p>
      {numbers === null ? (
        <>
          <p className="text-ink-muted">{t("rating.change.calibratingLine")}</p>
          <p className="font-display text-xl font-bold" data-testid="calibration">
            {t("rating.calibration", { count: change.calibration_left })}
          </p>
        </>
      ) : (
        <>
          <p className="flex items-baseline gap-3">
            <span className="sr-only">{t("rating.change.delta")}: </span>
            <span
              className={cn(
                "tabular font-display text-[28px] leading-none font-extrabold",
                numbers.delta > 0 ? "text-exact" : numbers.delta < 0 ? "text-miss" : "text-ink",
              )}
              data-testid="rating-delta"
            >
              {signedDelta(numbers.delta)}
            </span>
            <span className="tabular text-ink-muted">
              {t("rating.change.ratingLine", { old: numbers.old, next: numbers.next })}
            </span>
          </p>
          {change.tier && (
            <p data-testid="rating-tier">
              {tierChanged
                ? t("rating.change.newTier", { tier: tierLabel(change.tier) })
                : t("rating.change.tier", { tier: tierLabel(change.tier) })}
            </p>
          )}
        </>
      )}
      {change.leaver_penalty > 0 && (
        <p className="text-sm text-miss" data-testid="rating-penalty">
          {t("rating.change.penalty", { points: change.leaver_penalty })}
        </p>
      )}
    </div>
  );
}
