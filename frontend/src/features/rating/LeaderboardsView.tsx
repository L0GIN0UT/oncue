"use client";
/**
 * Лидеры `/leaderboards` по макету v3 (Leaderboard): переключатель раздела (музыка / кино) и сезон,
 * слева плитки «Ты» и «Друзья в таблице», справа вкладки «Рейтинг» и «Топ недели» с таблицей по
 * курсору. Данные — bff `/pages/leaderboards`, блоки гаснут независимо. Страница публичная: гость
 * видит таблицы без своего места.
 *
 * При смене раздела прошлая таблица остаётся на экране под индикатором обновления, переключатель
 * не пропадает; ошибки показываются рядом с таблицей и различаются по причине.
 */
import { useId, useState } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { blockData } from "@/shared/api/blocks";
import type { LadderCard, LeaderboardCard, LeaderboardEntry } from "@/shared/api/types";
import { Avatar } from "@/shared/ui/avatar";
import { Button } from "@/shared/ui/button";
import { ListRow } from "@/shared/ui/list-row";
import { PillTabs, type PillTab } from "@/shared/ui/pill-tabs";
import { Segmented } from "@/shared/ui/segmented";
import { TierBadge, tierKeyOf } from "@/shared/ui/tier-badge";
import { Tile } from "@/shared/ui/tile";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { useMyUserId } from "@/features/auth/use-my-user-id";
import { fetchFriends, friendsKeys } from "@/features/friends/api";
import { fetchLeaderboards } from "./leaderboards-api";
import { useLadderLabel, useTierLabel } from "./labels";
import { classifyBoardError, type BoardErrorKind } from "./logic";
import { SeasonLine } from "./RatingPanel";
import { WeeklyTopTable } from "./WeeklyTopTable";

const BOARD_KEY = ["page", "leaderboards"] as const;

const ERROR_TEXT = {
  notFound: "rating.board.notFound",
  login: "rating.board.loginRequired",
  staleCursor: "rating.board.staleCursor",
  invalidLadder: "rating.board.invalidLadder",
  failed: "rating.board.failed",
} as const satisfies Record<BoardErrorKind, string>;

type TabId = "rating" | "weekly";

/** Колонки таблицы: место, игрок, звание, рейтинг, матчи. */
const GRID = "grid grid-cols-[34px_minmax(0,1fr)_110px_72px_56px] items-center gap-3";
const ROW = "min-h-11 rounded-xl border-[1.5px] px-2.5 text-[15px]";

export function LeaderboardsView() {
  const t = useTranslations();
  const ladderLabel = useLadderLabel();
  const queryClient = useQueryClient();
  const viewer = useViewer();
  const isUser = viewer.kind === "user";
  const myId = useMyUserId();
  const panelId = useId();
  const [tab, setTab] = useState<TabId>("rating");
  const [picked, setPicked] = useState<string | null>(null);
  const [around, setAround] = useState(false);
  // Последний известный список разделов: переключатель не пропадает во время загрузки и ошибок.
  const [known, setKnown] = useState<LadderCard[] | null>(null);
  const friends = useQuery({ queryKey: friendsKeys.list, queryFn: fetchFriends, enabled: isUser });
  const friendIds = new Set(friends.data?.items.map((f) => f.user_id));

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
  const season = ladders?.find((l) => l.key === active)?.season ?? null;
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
  const tabs: PillTab<TabId>[] = [
    { value: "rating", label: t("rating.board.tabRating") },
    { value: "weekly", label: t("rating.board.weekly") },
  ];
  const friendRows = rows.filter((r) => friendIds.has(r.user_id));

  return (
    <div className="flex flex-col gap-5 pt-2" data-testid="leaderboards">
      <h1 className="sr-only">{t("rating.board.title")}</h1>
      <div className="flex flex-wrap items-center justify-between gap-3">
        {ladders ? (
          <Segmented
            aria-label={t("rating.board.ladders")}
            value={active ?? ""}
            options={ladders.map((l) => ({ value: l.key, label: ladderLabel(l.key) }))}
            onChange={choose}
          />
        ) : (
          !query.isPending && (
            <p className="text-ink-muted">{t("rating.board.ladderUnavailable")}</p>
          )
        )}
        {season && <SeasonLine season={season} />}
      </div>

      <section className="grid items-start gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="flex flex-col gap-5">
          {me ? (
            <MyPlace entry={me} ladder={active ? ladderLabel(active) : null} />
          ) : (
            <Tile cap={t("rating.board.me")} data-testid="my-place-empty">
              <p className="text-sm text-ink-muted">
                {isUser ? t("rating.board.noPlace") : t("rating.board.loginToSee")}
              </p>
            </Tile>
          )}
          {isUser && (
            <Tile cap={t("rating.board.friendsCap")} data-testid="board-friends">
              {friendRows.length === 0 ? (
                <p className="text-sm text-ink-muted">{t("rating.board.noFriends")}</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {friendRows.slice(0, 5).map((r) => (
                    <ListRow key={r.user_id} lead={`#${r.place}`} trailing={r.rating}>
                      <span dir="auto" className="font-semibold">
                        {r.nickname}
                      </span>
                    </ListRow>
                  ))}
                </div>
              )}
            </Tile>
          )}
        </div>

        <Tile
          cap={caption}
          aside={
            isUser && tab === "rating" ? (
              <button
                type="button"
                aria-pressed={around}
                onClick={() => setAround((v) => !v)}
                data-testid="around-me"
                className="h-8 rounded-full border-[1.5px] border-rule px-3 text-xs font-semibold hover:border-rule-strong"
              >
                {around ? t("rating.board.top") : t("rating.board.around")}
              </button>
            ) : undefined
          }
        >
          <PillTabs
            tabs={tabs}
            value={tab}
            onChange={setTab}
            panelId={panelId}
            aria-label={t("rating.board.tabs")}
          />
          <div id={panelId} role="tabpanel" className="flex flex-col gap-2">
            {tab === "rating" ? (
              <section
                aria-labelledby="board-table"
                className="flex flex-col gap-2"
                data-domain={ladders?.find((l) => l.key === active)?.domain}
              >
                <h2 id="board-table" className="sr-only">
                  {caption}
                </h2>
                {!isUser && (
                  <p className="text-[13px] text-ink-muted">{t("rating.board.loginToSee")}</p>
                )}
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
                  <div className="relative flex flex-col gap-3" aria-busy={query.isPlaceholderData}>
                    {query.isPlaceholderData && (
                      <p
                        role="status"
                        className="absolute inset-x-0 top-0 z-10 mx-auto w-fit rounded-full border-[1.5px] border-rule bg-stage-raised px-4 py-1 text-sm"
                        data-testid="board-updating"
                      >
                        {t("rating.board.updating")}
                      </p>
                    )}
                    <div
                      className={cn("flex flex-col gap-3", query.isPlaceholderData && "opacity-50")}
                    >
                      {rows.length === 0 ? (
                        <p className="text-ink-muted">{t("rating.board.empty")}</p>
                      ) : (
                        <BoardTable
                          rows={rows}
                          myId={myId}
                          friendIds={friendIds}
                          caption={caption}
                        />
                      )}
                      {errorKind && <BoardError kind={errorKind} onRecover={recover} />}
                      {query.hasNextPage && !errorKind && (
                        <Button
                          variant="secondary"
                          className="self-center"
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
            ) : !query.data ? (
              <p className="text-ink-muted" role="status">
                {t("common.loading")}
              </p>
            ) : weekly === null ? (
              <p className="text-ink-muted">{t("rating.board.weeklyUnavailable")}</p>
            ) : (
              <WeeklyTopTable top={weekly} heading="h2" />
            )}
          </div>
        </Tile>
      </section>
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

/** Плитка «Ты»: моё место, звание, рейтинг и матчи выбранного раздела. */
function MyPlace({ entry, ladder }: { entry: LeaderboardEntry; ladder: string | null }) {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  return (
    <Tile
      cap={t("rating.board.me")}
      aside={
        ladder && (
          <span className="rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted">
            {ladder}
          </span>
        )
      }
      data-testid="my-place"
    >
      <div className="flex items-center gap-3">
        <Avatar name={entry.nickname} className="size-[52px] border-[3px] border-music" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <span dir="auto" className="truncate text-base font-semibold">
            {entry.nickname}
          </span>
          <span className="text-xs text-ink-muted">
            {t("rating.board.place", { place: entry.place })}
          </span>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2">
        <span className="tabular font-display text-[28px] font-extrabold tracking-[-0.03em]">
          {entry.rating}
        </span>
        <TierBadge tier={tierKeyOf(entry.tier.family)}>{tierLabel(entry.tier)}</TierBadge>
      </div>
      <ListRow trailing={entry.matches_played}>{t("rating.board.colMatches")}</ListRow>
    </Tile>
  );
}

function BoardTable({
  rows,
  myId,
  friendIds,
  caption,
}: {
  rows: LeaderboardEntry[];
  myId: string | null;
  friendIds: ReadonlySet<string>;
  caption: string;
}) {
  const t = useTranslations();
  const tierLabel = useTierLabel();
  return (
    <div className="overflow-x-auto">
      <div
        role="table"
        aria-label={caption}
        className="flex min-w-[34rem] flex-col gap-1.5"
        data-testid="board"
      >
        <div role="row" className={cn(GRID, "px-2.5 text-xs text-ink-faint")}>
          <span role="columnheader">{t("rating.board.colPlace")}</span>
          <span role="columnheader">{t("rating.board.colPlayer")}</span>
          <span role="columnheader">{t("rating.board.colTier")}</span>
          <span role="columnheader" className="text-right">
            {t("rating.board.colRating")}
          </span>
          <span role="columnheader" className="text-right">
            {t("rating.board.colMatches")}
          </span>
        </div>
        {rows.map((r) => {
          const mine = r.user_id === myId;
          return (
            <div
              key={r.user_id}
              role="row"
              data-testid={`board-row-${r.place}`}
              aria-current={mine ? "true" : undefined}
              className={cn(
                GRID,
                ROW,
                "bg-stage-raised",
                mine ? "border-music" : "border-transparent",
              )}
            >
              <span
                role="cell"
                className={cn(
                  "tabular font-display font-extrabold",
                  mine || r.place <= 3 ? "text-music" : "text-ink-muted",
                )}
              >
                {r.place}
              </span>
              <span role="cell" className="flex min-w-0 items-center gap-2.5" dir="auto">
                <Avatar name={r.nickname} size="sm" className={mine ? "border-music" : undefined} />
                <span className="truncate font-semibold">
                  {r.nickname}
                  {mine && <span className="text-ink-muted"> ({t("rating.board.you")})</span>}
                  {!mine && friendIds.has(r.user_id) && (
                    <span className="font-normal text-ink-muted">
                      {" "}
                      · {t("rating.board.friend")}
                    </span>
                  )}
                </span>
              </span>
              <span role="cell">
                <TierBadge tier={tierKeyOf(r.tier.family)}>{tierLabel(r.tier)}</TierBadge>
              </span>
              <span role="cell" className="tabular text-right font-display text-sm font-extrabold">
                {r.rating}
              </span>
              <span role="cell" className="tabular text-right text-sm text-ink-muted">
                {r.matches_played}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
