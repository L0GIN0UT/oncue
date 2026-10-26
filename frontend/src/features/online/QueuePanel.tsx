"use client";
/**
 * Содержимое плитки «Рейтинговая дуэль» в хабе онлайна (4b, ADR 0038; макет «Онлайн»): переключатель
 * раздела (ladder), рейтинг и звание раздела строкой, одна главная кнопка «Найти соперника», ожидание с
 * heartbeat, отмена, подсказка «ищем дольше обычного», сигнал и переход в комнату при найденном
 * матче. Ladder-ы и форматы — из данных блока `queues`, названия — через i18n.
 *
 * Доступность: статусы поиска объявляются одной живой областью (без вложенных `role=status`),
 * ошибки — отдельным `role=alert`; фокус идёт за действием (отмена → в поиске, после отмены —
 * на кнопку входа, матч найден — на ссылку в комнату).
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { Chip } from "@/shared/ui/chip";
import { ListRow } from "@/shared/ui/list-row";
import { Segmented } from "@/shared/ui/segmented";
import { TierBadge, tierKeyOf } from "@/shared/ui/tier-badge";
import { useDynamicT } from "@/shared/i18n/dynamic";
import type { OnlineQueue, QueueFormat } from "@/shared/api/types";
import {
  useFormatLabel,
  useLadderLabel,
  useTierLabel,
  type RatingCard,
  type SeasonCard,
} from "@/features/rating/labels";
import { pointsToNext } from "@/features/rating/logic";
import { SeasonLine } from "@/features/rating/RatingPanel";
import { useMatchFound } from "./use-match-found";
import { useQueue } from "./use-queue";

const buttonKey = (ladder: string, format: string) => `${ladder}-${format}`;

type Props = {
  /** `null` — блок очередей недоступен. */
  queues: OnlineQueue[] | null;
  /** `null` — блок рейтингов недоступен. */
  ratings?: RatingCard[] | null;
  seasons?: SeasonCard[] | null;
};

export function QueuePanel({ queues, ratings = [], seasons = null }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const ladderLabel = useLadderLabel();
  const formatLabel = useFormatLabel();
  const { state, enter, leave, dismiss } = useQueue();
  const [pickedLadder, setPickedLadder] = useState<string | null>(null);
  const [pickedFormat, setPickedFormat] = useState<Record<string, QueueFormat>>({});

  const enterButton = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const roomLink = useRef<HTMLAnchorElement>(null);
  const prevPhase = useRef(state.phase);

  // Фокус следует за действием игрока.
  useEffect(() => {
    const prev = prevPhase.current;
    prevPhase.current = state.phase;
    if (prev === state.phase) return;
    if (state.phase === "searching" && prev === "entering") cancelButton.current?.focus();
    else if (state.phase === "idle" && prev === "searching") enterButton.current?.focus();
    else if (state.phase === "matched") roomLink.current?.focus();
  }, [state.phase]);

  useMatchFound(
    state.phase === "matched" ? state.roomCode : null,
    t("rating.queue.flash"),
    (code) => router.push(`/room/${code}`),
  );

  const busy = state.phase !== "idle" && state.phase !== "error";
  const ladders = [...new Set((queues ?? []).map((q) => q.ladder))];
  // Пока идёт поиск, на экране раздел, в котором ищут.
  const searchingLadder = state.phase === "searching" ? state.ladder : null;
  const ladder =
    searchingLadder ?? (pickedLadder && ladders.includes(pickedLadder) ? pickedLadder : ladders[0]);
  const pools = (queues ?? []).filter((q) => q.ladder === ladder);
  const format =
    pools.find((q) => q.format === pickedFormat[ladder ?? ""])?.format ?? pools[0]?.format;
  const pool = pools.find((q) => q.format === format);

  return (
    <section
      aria-label={t("rating.queue.title")}
      className="flex flex-col gap-3"
      data-testid="queue"
    >
      {queues === null ? (
        <p className="text-ink-muted" data-testid="queue-unavailable">
          {t("rating.queue.unavailable")}
        </p>
      ) : !ladder ? (
        <p className="text-ink-muted">{t("rating.queue.empty")}</p>
      ) : (
        <>
          {ladders.length > 1 && (
            <div className="flex">
              <Segmented
                aria-label={t("online.rating.ladders")}
                value={ladder}
                onChange={(next) => !busy && setPickedLadder(next)}
                options={ladders.map((l) => ({ value: l, label: ladderLabel(l) }))}
              />
            </div>
          )}
          <div
            data-domain={ladder}
            data-testid={`queue-ladder-${ladder}`}
            className="flex flex-col gap-3"
          >
            <RatingSummary
              ladder={ladder}
              ratings={ratings}
              season={seasons?.find((s) => s.ladder === ladder) ?? null}
            />
            <Button
              ref={enterButton}
              size="lg"
              disabled={busy || !format}
              onClick={() => format && void enter(ladder, format)}
              data-testid={`queue-enter-${buttonKey(ladder, format ?? "")}`}
              className="w-full"
            >
              {t("online.rating.find")}
            </Button>
          </div>
          {pools.length > 1 && (
            <div
              role="group"
              aria-label={t("online.rating.formats")}
              className="flex flex-wrap items-center gap-2"
            >
              {pools.map((q) => (
                <Chip
                  key={q.format}
                  selected={q.format === format}
                  disabled={busy}
                  onClick={() => setPickedFormat((cur) => ({ ...cur, [q.ladder]: q.format }))}
                  data-testid={`queue-format-${buttonKey(q.ladder, q.format)}`}
                >
                  {formatLabel(q.format)}
                </Chip>
              ))}
            </div>
          )}
          {pool && (
            <p className="text-center text-xs text-ink-muted" data-testid="queue-hint">
              {[
                dt(`rating.formatHint.${pool.format}`, ""),
                t("rating.queue.searchingCount", { count: pool.searching }),
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
        </>
      )}

      <div aria-live="polite" data-testid="queue-status">
        {state.phase === "entering" && (
          <p className="text-ink-muted">{t("rating.queue.entering")}</p>
        )}
        {state.phase === "searching" && (
          <div
            className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-music bg-stage p-5"
            data-testid="queue-searching"
          >
            <p className="flex items-center gap-2 font-medium">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
              {t("rating.queue.searching", {
                ladder: ladderLabel(state.ladder),
                format: formatLabel(state.format),
              })}
            </p>
            <p className="tabular text-sm text-ink-muted">
              {t("rating.queue.waited", { seconds: state.waitS })}
            </p>
            {state.slow && (
              <p className="text-sm text-ink-muted" data-testid="queue-slow">
                {t("rating.queue.slow")}
              </p>
            )}
            {state.notice && (
              <p className="text-sm text-miss" data-testid="queue-notice">
                {dt(state.notice, t("match.error.generic"))}
              </p>
            )}
            <Button
              ref={cancelButton}
              variant="secondary"
              className="self-start"
              disabled={state.leaving}
              onClick={() => void leave()}
              data-testid="queue-cancel"
            >
              {t("rating.queue.cancel")}
            </Button>
          </div>
        )}
        {state.phase === "matched" && (
          <div
            className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-music bg-stage p-5 shadow-[0_0_0_4px_var(--color-glow)]"
            data-testid="queue-found"
          >
            <p className="font-display text-lg font-semibold">{t("rating.queue.found")}</p>
            <Button asChild className="self-start">
              <Link ref={roomLink} href={`/room/${state.roomCode}`}>
                {t("rating.queue.toRoom")}
              </Link>
            </Button>
          </div>
        )}
      </div>
      {state.phase === "error" && (
        <div className="flex flex-col gap-2" data-testid="queue-error">
          <p role="alert" className="text-miss">
            {dt(state.errorKey, t("match.error.generic"))}
          </p>
          <div className="flex gap-3">
            {state.roomCode && (
              <Button asChild variant="secondary" size="sm">
                <Link href={`/room/${state.roomCode}`}>{t("rating.queue.toRoom")}</Link>
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={dismiss}>
              {t("rating.queue.dismiss")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

/** Рейтинг раздела строкой: очки и звание-значок; калибровка — вместо звания, путь до следующего — ниже. */
function RatingSummary({
  ladder,
  ratings,
  season,
}: {
  ladder: string;
  ratings: RatingCard[] | null;
  season: SeasonCard | null;
}) {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  const ladderLabel = useLadderLabel();
  const rating = ratings?.find((r) => r.ladder === ladder) ?? null;
  const left = rating ? pointsToNext(rating.rating, rating.next_tier_at) : null;
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid={`rating-${ladder}`}>
      {ratings === null ? (
        <p className="text-ink-muted" data-testid="ratings-unavailable">
          {t("rating.unavailable")}
        </p>
      ) : !rating ? (
        <p className="text-sm text-ink-muted">{t("rating.mine.empty")}</p>
      ) : rating.calibrating || !rating.tier ? (
        <>
          <p className="font-display text-xl font-bold" data-testid="calibration">
            {t("rating.calibration", { count: rating.calibration_left })}
          </p>
          <p className="text-sm text-ink-muted">{t("rating.calibrationHint")}</p>
        </>
      ) : (
        <>
          <ListRow
            lead={rating.rating}
            trailing={
              <span data-testid="tier">
                <TierBadge tier={tierKeyOf(rating.tier.family)} className="text-black">
                  {tierLabel(rating.tier)}
                </TierBadge>
              </span>
            }
          >
            {ladderLabel(ladder)}
          </ListRow>
          <p className="text-[13px] text-ink-muted" data-testid="next-tier">
            {left === null ? t("rating.topTier") : t("rating.toNext", { points: left })}
            {rating.place != null && <> · {t("rating.place", { place: rating.place })}</>}
          </p>
        </>
      )}
      {season && <SeasonLine season={season} />}
    </div>
  );
}
