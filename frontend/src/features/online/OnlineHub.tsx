"use client";
/**
 * Хаб онлайна `/online` по макету v3 (Online): рейтинговая дуэль, комната для друзей и друзья в
 * сети; ниже открытые лобби и последние матчи; полоса «Топ недели». Данные — bff
 * `/v1/pages/online`, блоки гаснут независимо; гостю bff отвечает `auth_required` — экран
 * регистрации (D13).
 */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Avatar } from "@/shared/ui/avatar";
import { ListRow } from "@/shared/ui/list-row";
import { Tile } from "@/shared/ui/tile";
import { StatusDot } from "@/features/friends/StatusDot";
import { useHasSanction } from "@/features/sanctions/use-sanctions";
import { useLadderLabel } from "@/features/rating/labels";
import { signedDelta } from "@/features/rating/logic";
import type { WeeklyTop } from "@/shared/api/types";
import { blockData, hasBlock, useOnlinePage } from "./bff";
import { CreateRoom } from "./CreateRoom";
import { JoinByCode } from "./JoinByCode";
import { MyMatchList, OpenRoomList } from "./lists";
import { OnlineAuthRequired } from "./OnlineGate";
import { QueuePanel } from "./QueuePanel";

const HUB_ROOMS = 5;
const HUB_MATCHES = 5;
const HUB_FRIENDS = 4;
const HUB_WEEKLY = 8;

const CHIP = "rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted";
const LINK = "text-xs text-ink-muted underline-offset-4 hover:text-ink hover:underline";

export function OnlineHub() {
  const t = useTranslations();
  const page = useOnlinePage();
  const banned = useHasSanction("online");

  if (page.isPending) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }
  if (page.isError) {
    return (
      <p className="pt-12 text-ink-muted" role="alert">
        {t("online.unavailable")}
      </p>
    );
  }
  // auth_required — гость; session_expired после неудачного refresh — снова войти.
  if (page.data.status !== "ok") return <OnlineAuthRequired />;

  const blocks = page.data.blocks;
  const queues = blockData(blocks?.queues);
  const modes = blockData(blocks?.modes);
  const rooms = blockData(blocks?.open_rooms);
  const matches = blockData(blocks?.my_matches);
  // Блоки 4b: пока bff их не отдаёт (null), секции не рисуем; unavailable — «не загрузилось».
  const weekly = blockData(blocks?.weekly);
  const friends = blockData(blocks?.friends);
  const searching = (queues ?? []).reduce((sum, q) => sum + q.searching, 0);
  const friendsOnline = (friends?.items ?? []).filter((f) => f.status !== "offline").length;
  const unavailable = <p className="text-sm text-ink-muted">{t("online.blockUnavailable")}</p>;

  return (
    <div className="flex flex-col gap-5 pt-2" data-testid="online">
      <h1 className="sr-only">{t("online.title")}</h1>

      {banned && (
        <p
          role="status"
          data-testid="online-banned"
          className="rounded-[var(--radius-control)] border-[1.5px] border-partial-line bg-partial-fill px-4 py-3"
        >
          {t("online.banned")}{" "}
          <Link href="/sanctions" className="underline underline-offset-4">
            {t("social.sanctions.details")}
          </Link>
        </p>
      )}

      <section className="grid items-stretch gap-5 md:grid-cols-2 lg:grid-cols-3">
        {hasBlock(blocks?.queues) ? (
          <Tile
            accent
            cap={t("online.ranked.cap")}
            aside={
              queues &&
              searching > 0 && (
                <span className={CHIP}>{t("rating.queue.searchingCount", { count: searching })}</span>
              )
            }
            data-testid="online-ranked"
          >
            <QueuePanel
              queues={queues}
              ratings={hasBlock(blocks?.ratings) ? blockData(blocks?.ratings) : []}
              seasons={blockData(blocks?.seasons)}
            />
          </Tile>
        ) : (
          <Tile cap={t("online.ranked.cap")} data-testid="online-ranked">
            {unavailable}
          </Tile>
        )}

        <Tile
          cap={t("online.room.cap")}
          aside={<span className={CHIP}>{t("online.room.upTo")}</span>}
          data-testid="online-room"
        >
          <CreateRoom modes={modes} />
          <JoinByCode />
          <span className="text-xs text-ink-muted">{t("online.room.foot")}</span>
        </Tile>

        <Tile
          cap={t("online.friends.cap", { count: friendsOnline })}
          aside={
            <Link href="/friends" className={LINK} data-testid="hub-friends-all">
              {friends?.incoming_requests
                ? t("social.hub.allWithRequests", { count: friends.incoming_requests })
                : t("social.hub.all")}
            </Link>
          }
          data-testid="hub-friends"
        >
          {friends === null && hasBlock(blocks?.friends) ? (
            unavailable
          ) : !friends || friends.items.length === 0 ? (
            <>
              <p className="text-sm text-ink-muted">{t("social.friends.empty")}</p>
              <Link
                href="/friends"
                className="mt-auto grid h-[34px] place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised text-[13px] font-semibold hover:border-rule-strong"
              >
                {t("online.friends.add")}
              </Link>
            </>
          ) : (
            <div className="flex flex-col gap-1.5">
              {friends.items.slice(0, HUB_FRIENDS).map((f) => (
                <ListRow
                  key={f.user_id}
                  lead={<Avatar name={f.nickname} size="sm" className="size-7 text-[10px]" />}
                  trailing={<StatusDot status={f.status} />}
                  data-testid={`hub-friend-${f.nickname}`}
                >
                  <span dir="auto" className="font-semibold">
                    {f.nickname}
                  </span>
                </ListRow>
              ))}
            </div>
          )}
        </Tile>
      </section>

      <section className="grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Tile
          cap={
            rooms
              ? t("online.lobbies.cap", { count: rooms.items.length })
              : t("online.lobbies.title")
          }
          aside={
            <Link href="/lobbies" className={LINK}>
              {t("online.lobbies.all")}
            </Link>
          }
          data-testid="online-lobbies"
        >
          {rooms === null ? (
            unavailable
          ) : (
            <>
              <OpenRoomList rooms={rooms.items.slice(0, HUB_ROOMS)} />
              {rooms.items.length === 0 && (
                <Link
                  href="/lobbies"
                  className="grid h-[34px] w-fit place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised px-4 text-[13px] font-semibold hover:border-rule-strong"
                >
                  {t("online.lobbies.create")}
                </Link>
              )}
            </>
          )}
        </Tile>

        <Tile
          cap={t("online.myMatches.cap")}
          aside={
            <Link href="/me" className={LINK}>
              {t("online.myMatches.all")}
            </Link>
          }
          data-testid="online-matches"
        >
          {matches === null ? (
            unavailable
          ) : (
            <MyMatchList matches={matches.items.slice(0, HUB_MATCHES)} />
          )}
        </Tile>
      </section>

      {hasBlock(blocks?.weekly) && (
        <Tile
          cap={t("online.weekly.cap")}
          aside={
            <Link href="/leaderboards" className={LINK}>
              {t("rating.board.fullTable")}
            </Link>
          }
          data-testid="online-weekly"
        >
          {weekly === null ? (
            <p className="text-sm text-ink-muted">{t("rating.board.weeklyUnavailable")}</p>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 md:gap-8">
              {weekly.map((top) => (
                <WeeklyStrip key={top.ladder} top={top} limit={weekly.length > 1 ? 5 : HUB_WEEKLY} />
              ))}
            </div>
          )}
        </Tile>
      )}
    </div>
  );
}

/** Полоса топа недели одного раздела: аватар, ник и прирост рейтинга с понедельника (ADR 0039). */
function WeeklyStrip({ top, limit }: { top: WeeklyTop; limit: number }) {
  const t = useTranslations();
  const ladderLabel = useLadderLabel();
  const items = top.items.slice(0, limit);
  return (
    <div className="flex flex-col gap-2" data-testid={`weekly-${top.ladder}`}>
      <span className="text-xs text-ink-muted">{ladderLabel(top.ladder)}</span>
      {items.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("rating.board.weeklyEmpty")}</p>
      ) : (
        <ol className="flex flex-wrap justify-between gap-3">
          {items.map((r) => (
            <li
              key={r.user_id}
              value={r.place}
              data-testid={`weekly-row-${r.place}`}
              className="flex min-w-0 flex-col items-center gap-1"
            >
              <Avatar name={r.nickname} />
              <span dir="auto" className="max-w-[84px] truncate text-xs font-semibold">
                {r.nickname}
              </span>
              <span className="tabular text-xs text-ink-muted">{signedDelta(r.delta)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
