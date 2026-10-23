"use client";
/**
 * Лидерборды `/leaderboards` (4b): переключатель ladder-ов, таблица с пагинацией по курсору,
 * «Моё место» (`around=me`, только вошедшему) и топ недели. Данные — bff `/pages/leaderboards`,
 * блоки гаснут независимо. Страница публичная: гость видит таблицы без своего места.
 *
 * При смене ladder-а прошлая таблица остаётся на экране под индикатором обновления, кнопки
 * ladder-ов не пропадают; ошибки показываются рядом с таблицей и различаются по причине.
 */
import { useState } from "react";
import { keepPreviousData, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { blockData } from "@/shared/api/blocks";
import type { LadderCard, LeaderboardCard, LeaderboardEntry } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { useMyUserId } from "@/features/auth/use-my-user-id";
import { fetchLeaderboards } from "./leaderboards-api";
import { useLadderLabel, useTierLabel } from "./labels";
import { classifyBoardError, type BoardErrorKind } from "./logic";
import { WeeklyTopTable } from "./WeeklyTopTable";

const BOARD_KEY = ["page", "leaderboards"] as const;

const ERROR_TEXT = {
  notFound: "rating.board.notFound",
  login: "rating.board.loginRequired",
  staleCursor: "rating.board.staleCursor",
  invalidLadder: "rating.board.invalidLadder",
  failed: "rating.board.failed",
} as const satisfies Record<BoardErrorKind, string>;

export function LeaderboardsView() {
  const t = useTranslations();
  const ladderLabel = useLadderLabel();
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const isUser = viewer.kind === "user";
  const myId = useMyUserId();
  const [picked, setPicked] = useState<string | null>(null);
  const [around, setAround] = useState(false);
  // Последний известный список ladder-ов: кнопки не пропадают во время загрузки и ошибок.
  const [known, setKnown] = useState<LadderCard[] | null>(null);

  const query = useInfiniteQuery({
    queryKey: [...BOARD_KEY, picked, around],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      fetchLeaderboards({ ladder: picked, cursor: pageParam, around: around ? "me" : null }),
    getNextPageParam: (last) => blockData(last.blocks.leaderboard)?.next_cursor ?? null,
    placeholderData: keepPreviousData,
    retry: false,
  });

  const first = query.data?.pages[0]?.blocks;
  const fresh = blockData(first?.ladders);
  if (fresh && fresh !== known) setKnown(fresh);
  const ladders = known;
  const boards = (query.data?.pages ?? [])
    .map((p) => blockData(p.blocks.leaderboard))
    .filter((b): b is LeaderboardCard => b !== null);
  const rows = boards.flatMap((b) => b.items);
  const me = boards.find((b) => b.me)?.me ?? null;
  const active = picked ?? boards[0]?.ladder ?? ladders?.[0]?.key ?? null;
  const weekly = blockData(first?.weekly);
  const errorKind = query.isError
    ? classifyBoardError(query.error, query.isFetchNextPageError)
    : null;

  const choose = (ladder: string | null) => {
    setPicked(ladder);
    setAround(false);
  };
  const recover = (kind: BoardErrorKind) => {
    if (kind === "login") setAround(false);
    else if (kind === "staleCursor") void queryClient.resetQueries({ queryKey: BOARD_KEY });
    else if (kind === "failed") void query.refetch();
    else choose(null);
  };
  const caption = `${t("rating.board.leaderboard")}${active ? ` · ${ladderLabel(active)}` : ""}`;

  return (
    <div className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex max-w-3xl flex-col gap-3">
        <h1 className="font-display text-3xl leading-tight font-bold sm:text-4xl">
          {t("rating.board.title")}
        </h1>
        <p className="max-w-[60ch] text-ink-muted">{t("rating.board.lead")}</p>
      </header>

      {ladders && (
        <div
          role="group"
          aria-label={t("rating.board.ladders")}
          className="flex flex-wrap gap-2"
          data-testid="ladders"
        >
          {ladders.map((l) => (
            <Button
              key={l.key}
              variant={l.key === active ? "primary" : "secondary"}
              size="sm"
              aria-pressed={l.key === active}
              data-domain={l.domain}
              data-testid={`ladder-${l.key}`}
              onClick={() => choose(l.key)}
            >
              {ladderLabel(l.key)}
            </Button>
          ))}
        </div>
      )}
      {!ladders && !query.isPending && (
        <p className="text-ink-muted">{t("rating.board.ladderUnavailable")}</p>
      )}

      <section
        aria-labelledby="board-table"
        className="flex flex-col gap-4"
        data-domain={ladders?.find((l) => l.key === active)?.domain}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 id="board-table" className="font-display text-xl font-semibold">
            {caption}
          </h2>
          {isUser ? (
            <Button
              variant="secondary"
              size="sm"
              aria-pressed={around}
              onClick={() => setAround((v) => !v)}
              data-testid="around-me"
            >
              {around ? t("rating.board.top") : t("rating.board.around")}
            </Button>
          ) : (
            <p className="text-sm text-ink-muted">{t("rating.board.loginToSee")}</p>
          )}
        </div>

        {query.isPending && !query.data ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : errorKind && !query.data ? (
          <BoardError kind={errorKind} onRecover={recover} />
        ) : boards.length === 0 ? (
          <p className="text-ink-muted" data-testid="board-unavailable">
            {t("rating.unavailable")}
          </p>
        ) : (
          <div className="relative flex flex-col gap-4" aria-busy={query.isPlaceholderData}>
            {query.isPlaceholderData && (
              <p
                role="status"
                className="absolute inset-x-0 top-0 z-10 mx-auto w-fit rounded-full bg-stage-raised px-4 py-1 text-sm ring-1 ring-rule"
                data-testid="board-updating"
              >
                {t("rating.board.updating")}
              </p>
            )}
            <div className={cn("flex flex-col gap-4", query.isPlaceholderData && "opacity-50")}>
              {me && !rows.some((r) => r.user_id === me.user_id) && <MyPlace entry={me} />}
              {rows.length === 0 ? (
                <p className="text-ink-muted">{t("rating.board.empty")}</p>
              ) : (
                <BoardTable rows={rows} myId={myId} caption={caption} />
              )}
              {errorKind && <BoardError kind={errorKind} onRecover={recover} />}
              {query.hasNextPage && !errorKind && (
                <Button
                  variant="secondary"
                  className="self-start"
                  disabled={query.isFetchingNextPage}
                  onClick={() => void query.fetchNextPage()}
                  data-testid="board-more"
                >
                  {t("rating.board.more")}
                </Button>
              )}
            </div>
          </div>
        )}
      </section>

      {query.data && (
        <section aria-labelledby="board-weekly" className="flex flex-col gap-4">
          <h2 id="board-weekly" className="sr-only">
            {t("rating.board.weekly")}
          </h2>
          {weekly === null ? (
            <p className="text-ink-muted">{t("rating.board.weeklyUnavailable")}</p>
          ) : (
            <WeeklyTopTable top={weekly} heading="h3" />
          )}
        </section>
      )}
    </div>
  );
}

function BoardError({
  kind,
  onRecover,
}: {
  kind: BoardErrorKind;
  onRecover: (kind: BoardErrorKind) => void;
}) {
  const t = useTranslations();
  return (
    <div className="flex flex-col items-start gap-3" role="alert" data-testid="board-error">
      <p className="text-ink-muted" data-kind={kind}>
        {t(ERROR_TEXT[kind])}
      </p>
      <Button variant="secondary" size="sm" onClick={() => onRecover(kind)}>
        {kind === "failed" ? t("common.retry") : t("rating.board.reset")}
      </Button>
    </div>
  );
}

function MyPlace({ entry }: { entry: LeaderboardEntry }) {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  return (
    <p
      className="tabular rounded-[var(--radius-control)] bg-stage-raised px-4 py-3 ring-1 ring-[var(--accent)]"
      data-testid="my-place"
    >
      {t("rating.board.me")}: {entry.place} · {tierLabel(entry.tier)} ·{" "}
      {t("rating.points", { rating: entry.rating })}
    </p>
  );
}

function BoardTable({
  rows,
  myId,
  caption,
}: {
  rows: LeaderboardEntry[];
  myId: string | null;
  caption: string;
}) {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  return (
    <div className="overflow-x-auto rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule">
      <table className="w-full min-w-[32rem] text-left" data-testid="board">
        <caption className="sr-only">{caption}</caption>
        <thead className="text-sm text-ink-muted">
          <tr>
            <th scope="col" className="px-4 py-3 font-medium">
              {t("rating.board.colPlace")}
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              {t("rating.board.colPlayer")}
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              {t("rating.board.colTier")}
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              {t("rating.board.colRating")}
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              {t("rating.board.colMatches")}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {rows.map((r) => (
            <tr
              key={r.user_id}
              data-testid={`board-row-${r.place}`}
              aria-current={r.user_id === myId ? "true" : undefined}
              className={cn(r.user_id === myId && "bg-stage-raised")}
            >
              <td className="tabular px-4 py-3 font-display font-bold text-[var(--accent)]">
                {r.place}
              </td>
              <td className="max-w-48 truncate px-4 py-3" dir="auto">
                {r.nickname}
                {r.user_id === myId && (
                  <span className="text-sm text-ink-faint"> ({t("rating.board.you")})</span>
                )}
              </td>
              <td className="px-4 py-3">{tierLabel(r.tier)}</td>
              <td className="tabular px-4 py-3 text-right">{r.rating}</td>
              <td className="tabular px-4 py-3 text-right text-ink-muted">{r.matches_played}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
