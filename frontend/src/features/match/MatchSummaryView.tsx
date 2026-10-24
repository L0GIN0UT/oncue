"use client";
/**
 * Итог матча (bff `/v1/pages/match/{id}`): места с ничьими, очки, исход дуэли («Соперник
 * сдался», тех. победа), раунды с единицами и «Смотреть позже» (медиатека, как в соло-итоге),
 * оценка соперников (похвала или жалоба → `PlayerRated`, D14). Действия комнаты (реванш) —
 * снаружи через `actions`. Пока итог пишется, места берутся из дельты `summary`.
 */
import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Flag, ThumbsUp } from "lucide-react";
import { isApiError } from "@/shared/api/errors";
import type { Domain, ItemCard } from "@/shared/api/types";
import type { SummaryDelta } from "@/shared/realtime";
import { Button } from "@/shared/ui/button";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { PlayerBadge } from "@/features/cosmetics/PlayerBadge";
import type { AppearanceLike } from "@/features/cosmetics/slots";
import { SaveButton } from "@/features/library/SaveButton";
import { blockData, useMatchPage, type MatchSummary } from "@/features/online/bff";
import { useModeTitle } from "@/features/online/lists";
import { RatingChangeBlock } from "@/features/rating/RatingChangeBlock";
import { rateOpponent } from "./api";
import { matchErrorKey } from "./errors";

type Placement = SummaryDelta["placements"][number] & { nickname: string };

type Props = {
  matchId: string;
  myId: string | null;
  /** Места из канала (до записи итога) и ники из комнаты. */
  fallback?: { placements: Placement[]; domain: Domain } | null;
  actions?: ReactNode;
};

export function MatchSummaryView({ matchId, myId, fallback, actions }: Props) {
  const t = useTranslations();
  const page = useMatchPage(matchId);
  const summary = blockData(page.data?.blocks.match);
  const items = new Map<string, ItemCard>(
    (blockData(page.data?.blocks.items) ?? []).map((i) => [i.media_item_id, i]),
  );

  // Оформление игроков — блок bff `appearance` (`null`/недоступен — итог без оформления).
  const appearances = new Map<string, AppearanceLike>(
    (blockData(page.data?.blocks.appearance) ?? []).map((a) => [a.user_id, a.appearance]),
  );

  const placements: Placement[] =
    summary?.players.map((p) => ({ ...p })) ?? fallback?.placements ?? [];
  const domain = summary?.mode.domain ?? fallback?.domain ?? "music";

  if (!summary && !fallback) {
    if (page.isError) {
      const missing = isApiError(page.error) && page.error.status === 404;
      return (
        <p className="pt-12 text-ink-muted" role="alert">
          {t(missing ? "match.summary.notFound" : "match.summary.failed")}
        </p>
      );
    }
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-10" data-domain={domain} data-testid="match-summary">
      <Headline summary={summary} placements={placements} myId={myId} />
      <Standings placements={placements} myId={myId} appearances={appearances} />
      {actions}
      {/* Дельта рейтинга — отдельный блок со своим опросом: итог не ждёт rating. */}
      {summary?.mode.ranked &&
        summary.finish !== "aborted" &&
        myId &&
        placements.some((p) => p.user_id === myId) && (
          <RatingChangeBlock matchId={matchId} myId={myId} />
        )}
      {summary && <Rounds summary={summary} items={items} domain={domain} />}
      {summary && myId && placements.some((p) => p.user_id === myId) && (
        <RateOpponents matchId={matchId} myId={myId} players={placements} />
      )}
    </div>
  );
}

function Headline({
  summary,
  placements,
  myId,
}: {
  summary: MatchSummary | null;
  placements: Placement[];
  myId: string | null;
}) {
  const t = useTranslations();
  const modeTitle = useModeTitle();
  const mine = placements.find((p) => p.user_id === myId);
  const opponentForfeit = placements.some((p) => p.user_id !== myId && p.finish === "forfeit");
  let line: string;
  if (summary?.finish === "aborted") line = t("match.summary.aborted");
  else if (!mine) line = t("match.summary.title");
  else if (mine.outcome === "win" && opponentForfeit) line = t("match.summary.opponentForfeit");
  else if (mine.finish === "tech_win") line = t("match.summary.techWin");
  else if (mine.outcome) line = t(`online.outcome.${mine.outcome}`);
  else line = t("match.summary.place", { place: mine.place });
  return (
    <header className="flex flex-col gap-2 pt-2">
      {summary && <p className="text-[var(--accent)]">{modeTitle(summary.mode)}</p>}
      <h1 className="font-display text-3xl font-bold sm:text-4xl" data-testid="summary-headline">
        {line}
      </h1>
      {mine && (
        <p className="tabular text-ink-muted" data-testid="summary-score">
          {t("match.summary.myScore", { score: mine.score, place: mine.place })}
        </p>
      )}
    </header>
  );
}

function Standings({
  placements,
  myId,
  appearances,
}: {
  placements: Placement[];
  myId: string | null;
  appearances: Map<string, AppearanceLike>;
}) {
  const t = useTranslations();
  const sorted = [...placements].sort((a, b) => a.place - b.place || b.score - a.score);
  return (
    <ol
      className="flex flex-col gap-2"
      aria-label={t("match.summary.standings")}
      data-testid="standings"
    >
      {sorted.map((p) => (
        <li
          key={p.user_id}
          className={cn(
            "flex items-center gap-4 rounded-[var(--radius-control)] bg-stage px-4 py-3 ring-1 ring-rule",
            p.user_id === myId && "ring-[var(--accent)]",
          )}
          data-testid={`standing-${p.nickname}`}
        >
          <span className="tabular w-8 font-display text-2xl font-bold text-[var(--accent)]">
            {p.place}
          </span>
          <PlayerBadge
            className="flex-1"
            nickname={p.nickname}
            appearance={appearances.get(p.user_id)}
            suffix={
              p.user_id === myId && <span className="text-sm text-ink-faint">{t("match.you")}</span>
            }
          />
          {p.finish !== "completed" && p.finish !== "tech_win" && (
            <span className="text-sm text-ink-muted" data-testid="standing-finish">
              {t(`online.finish.${p.finish}`)}
            </span>
          )}
          <span className="tabular font-display text-xl font-semibold">{p.score}</span>
        </li>
      ))}
    </ol>
  );
}

function Rounds({
  summary,
  items,
  domain,
}: {
  summary: MatchSummary;
  items: Map<string, ItemCard>;
  domain: Domain;
}) {
  const t = useTranslations();
  const names = new Map(summary.players.map((p) => [p.user_id, p.nickname]));
  return (
    <section aria-labelledby="summary-rounds" className="flex flex-col gap-3">
      <h2 id="summary-rounds" className="font-display text-xl font-semibold">
        {t("match.summary.rounds")}
      </h2>
      <ol className="flex flex-col divide-y divide-rule rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule">
        {summary.rounds.map((r) => {
          const item = items.get(r.media_item_id);
          const winners = r.results.filter((x) => x.level !== null);
          return (
            <li
              key={r.index}
              className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-4"
              data-testid={`summary-round-${r.index}`}
            >
              <span className="tabular w-6 text-ink-faint">{r.index + 1}</span>
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="font-medium" dir="auto">
                  {r.void
                    ? t("match.summary.void")
                    : (item?.title ?? t("match.summary.unknownItem"))}
                  {item?.year ? <span className="text-ink-muted"> · {item.year}</span> : null}
                </span>
                <span className="text-sm text-ink-muted">
                  {winners.length
                    ? winners
                        .map((w) =>
                          t("match.summary.guessedBy", {
                            name: names.get(w.user_id) ?? "",
                            level: w.level ?? 0,
                          }),
                        )
                        .join(", ")
                    : t("match.summary.nobody")}
                </span>
              </div>
              {!r.void && (
                <SaveButton mediaItemId={r.media_item_id} source="results" domain={domain} />
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RateOpponents({
  matchId,
  myId,
  players,
}: {
  matchId: string;
  myId: string;
  players: Placement[];
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [done, setDone] = useState<Record<string, "praise" | "report">>({});
  const [error, setError] = useState<string | null>(null);
  const others = players.filter((p) => p.user_id !== myId);
  if (!others.length) return null;

  const rate = async (userId: string, kind: "praise" | "report") => {
    setError(null);
    try {
      await rateOpponent(matchId, userId, kind);
      setDone((d) => ({ ...d, [userId]: kind }));
    } catch (e) {
      if (isApiError(e, "match.already_rated")) setDone((d) => ({ ...d, [userId]: kind }));
      else setError(dt(matchErrorKey(e), t("match.error.generic")));
    }
  };

  return (
    <section aria-labelledby="summary-rate" className="flex flex-col gap-3">
      <h2 id="summary-rate" className="font-display text-xl font-semibold">
        {t("match.rate.title")}
      </h2>
      <ul className="flex flex-col gap-2">
        {others.map((p) => (
          <li
            key={p.user_id}
            className="flex flex-wrap items-center gap-3"
            data-testid={`rate-${p.nickname}`}
          >
            <span className="min-w-32 font-medium" dir="auto">
              {p.nickname}
            </span>
            {done[p.user_id] ? (
              <span className="text-sm text-ink-muted" role="status">
                {t(done[p.user_id] === "praise" ? "match.rate.praised" : "match.rate.reported")}
              </span>
            ) : (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => rate(p.user_id, "praise")}
                  data-testid="rate-praise"
                >
                  <ThumbsUp aria-hidden />
                  {t("match.rate.praise")}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => rate(p.user_id, "report")}
                  data-testid="rate-report"
                >
                  <Flag aria-hidden />
                  {t("match.rate.report")}
                </Button>
              </>
            )}
          </li>
        ))}
      </ul>
      {error && (
        <p role="alert" className="text-sm text-miss">
          {error}
        </p>
      )}
    </section>
  );
}
