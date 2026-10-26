"use client";
/**
 * Хаб онлайна `/online` (макет «Онлайн»): блок рейтинговой дуэли с кнопкой «Найти соперника» и
 * вкладки — лобби (с созданием комнаты), друзья в сети, мои матчи, топ недели, вход по коду.
 * Данные — bff `/v1/pages/online`, блоки гаснут независимо; гостю bff отвечает `auth_required` —
 * экран регистрации (D13).
 */
import Link from "next/link";
import { useId, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Domain } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { Avatar } from "@/shared/ui/avatar";
import { Button } from "@/shared/ui/button";
import { TabPanel, Tabs } from "@/shared/ui/tabs";
import { useIcons } from "@/shared/ui/theme-provider";
import { createRoom } from "@/features/match/api";
import { matchErrorKey } from "@/features/match/errors";
import { StatusDot } from "@/features/friends/StatusDot";
import { HubHeader } from "@/features/hubs/HubHeader";
import { useHasSanction } from "@/features/sanctions/use-sanctions";
import { WeeklyTopTable } from "@/features/rating/WeeklyTopTable";
import { blockData, hasBlock, useOnlinePage, type OnlineModeCard } from "./bff";
import { JoinByCode } from "./JoinByCode";
import { MyMatchList, OpenRoomList } from "./lists";
import { OnlineAuthRequired } from "./OnlineGate";
import { QueuePanel } from "./QueuePanel";

const HUB_ROOMS = 5;
const HUB_MATCHES = 5;
const HUB_WEEKLY = 3;
const HUB_FRIENDS = 6;
const DOMAINS: Domain[] = ["music", "film"];

type HubTab = "lobbies" | "friends" | "matches" | "weekly" | "code";

export function OnlineHub() {
  const t = useTranslations();
  const idBase = useId();
  const page = useOnlinePage();
  const banned = useHasSanction("online");
  const [tab, setTab] = useState<HubTab>("lobbies");

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

  const tabs: { value: HubTab; label: string }[] = [
    {
      value: "lobbies",
      label: rooms
        ? t("online.tabs.lobbies", { count: rooms.items.length })
        : t("online.tabs.lobbiesPlain"),
    },
    ...(hasBlock(blocks?.friends)
      ? [{ value: "friends" as const, label: t("online.tabs.friends", { count: friendsOnline }) }]
      : []),
    { value: "matches", label: t("online.tabs.matches") },
    ...(hasBlock(blocks?.weekly)
      ? [{ value: "weekly" as const, label: t("online.tabs.weekly") }]
      : []),
    { value: "code", label: t("online.tabs.code") },
  ];

  const unavailable = <p className="py-2 text-ink-muted">{t("online.blockUnavailable")}</p>;

  return (
    <div className="flex flex-col gap-4 pt-4">
      <HubHeader
        title={t("online.title")}
        aside={
          queues && searching > 0 ? t("rating.queue.searchingCount", { count: searching }) : null
        }
      />

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

      {hasBlock(blocks?.queues) && (
        <QueuePanel
          queues={queues}
          ratings={hasBlock(blocks?.ratings) ? blockData(blocks?.ratings) : []}
          seasons={blockData(blocks?.seasons)}
        />
      )}
      <Link
        href="/leaderboards"
        className="self-center text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
      >
        {t("rating.board.fullTable")}
      </Link>

      <Tabs
        aria-label={t("online.tabs.label")}
        idBase={idBase}
        tabs={tabs}
        value={tab}
        onChange={setTab}
        className="mt-1.5"
      />

      <TabPanel idBase={idBase} value="lobbies" active={tab === "lobbies"}>
        <div className="flex flex-col gap-3.5 pt-3.5">
          {rooms === null ? unavailable : <OpenRoomList rooms={rooms.items.slice(0, HUB_ROOMS)} />}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CreateRoom modes={modes} />
            <Link
              href="/lobbies"
              className="text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
            >
              {t("online.lobbies.all")}
            </Link>
          </div>
        </div>
      </TabPanel>

      {hasBlock(blocks?.friends) && (
        <TabPanel idBase={idBase} value="friends" active={tab === "friends"}>
          <div className="flex flex-col gap-3.5 pt-3.5" data-testid="hub-friends">
            {friends === null ? (
              unavailable
            ) : friends.items.length === 0 ? (
              <p className="py-2 text-ink-muted">{t("social.friends.empty")}</p>
            ) : (
              <ul className="flex flex-col gap-2.5">
                {friends.items.slice(0, HUB_FRIENDS).map((f) => (
                  <li
                    key={f.user_id}
                    data-testid={`hub-friend-${f.nickname}`}
                    className="flex min-h-[64px] items-center gap-3.5 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px]"
                  >
                    <Avatar name={f.nickname} />
                    <span dir="auto" className="min-w-0 flex-1 truncate font-semibold">
                      {f.nickname}
                    </span>
                    <StatusDot status={f.status} />
                  </li>
                ))}
              </ul>
            )}
            <Link
              href="/friends"
              className="self-start text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
              data-testid="hub-friends-all"
            >
              {friends?.incoming_requests
                ? t("social.hub.allWithRequests", { count: friends.incoming_requests })
                : t("social.hub.all")}
            </Link>
          </div>
        </TabPanel>
      )}

      <TabPanel idBase={idBase} value="matches" active={tab === "matches"}>
        <div className="flex flex-col gap-3.5 pt-3.5">
          {matches === null ? (
            unavailable
          ) : (
            <MyMatchList matches={matches.items.slice(0, HUB_MATCHES)} />
          )}
          <Link
            href="/me"
            className="self-start text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
          >
            {t("online.myMatches.all")}
          </Link>
        </div>
      </TabPanel>

      {hasBlock(blocks?.weekly) && (
        <TabPanel idBase={idBase} value="weekly" active={tab === "weekly"}>
          <div className="flex flex-col gap-6 pt-3.5">
            {weekly === null ? (
              <p className="py-2 text-ink-muted">{t("rating.board.weeklyUnavailable")}</p>
            ) : (
              weekly.map((top) => <WeeklyTopTable key={top.ladder} top={top} limit={HUB_WEEKLY} />)
            )}
          </div>
        </TabPanel>
      )}

      <TabPanel idBase={idBase} value="code" active={tab === "code"}>
        <div className="pt-3.5">
          <JoinByCode />
        </div>
      </TabPanel>
    </div>
  );
}

/** «Создать комнату для друзей»: кнопка раскрывает режимы строками; комната создаётся по клику. */
function CreateRoom({ modes }: { modes: OnlineModeCard[] | null }): ReactNode {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const icons = useIcons();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const Plus = icons.plus;

  const create = async (mode: OnlineModeCard) => {
    setBusy(mode.key);
    setError(null);
    try {
      const room = await createRoom({ mode_key: mode.key });
      router.push(`/room/${room.code}`);
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(null);
    }
  };

  return (
    <div className="flex w-full flex-col gap-3">
      <Button
        variant="secondary"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="self-start"
        data-testid="create-room-toggle"
      >
        <Plus aria-hidden size={18} />
        {t("online.create.toggle")}
      </Button>
      {open && (
        <div id={panelId} className="flex animate-rise flex-col gap-3.5">
          {modes === null ? (
            <p className="text-ink-muted">{t("online.blockUnavailable")}</p>
          ) : !modes.length ? (
            <p className="text-ink-muted">{t("online.create.empty")}</p>
          ) : (
            DOMAINS.map((domain) => {
              // Рейтинговые комнаты — только из очереди (4b); режимы по сборнику — со страницы сборника.
              const list = modes.filter(
                (m) => m.domain === domain && !m.online.ranked && !m.online.max_collections,
              );
              if (!list.length) return null;
              return (
                <div key={domain} data-domain={domain} className="flex flex-col gap-2">
                  <h3 className="px-0.5 text-[13px] text-ink-muted">{t(`domain.${domain}`)}</h3>
                  <ul className="flex flex-col gap-2.5">
                    {list.map((mode) => (
                      <li key={mode.key}>
                        <button
                          type="button"
                          disabled={busy !== null}
                          onClick={() => create(mode)}
                          data-testid={`create-room-${mode.key}`}
                          className="flex min-h-[64px] w-full flex-col justify-center gap-0.5 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-2 text-left transition-colors hover:border-rule-strong hover:bg-stage disabled:opacity-45"
                        >
                          <span className="font-semibold">{dt(mode.title_key, mode.key)}</span>
                          <span className="text-[13px] text-ink-muted">
                            {busy === mode.key
                              ? t("online.create.busy")
                              : t("online.create.rules", {
                                  kind: t(`online.kind.${mode.online.kind}`),
                                  rounds: mode.online.rounds,
                                  min: mode.online.players_min,
                                  max: mode.online.players_max,
                                })}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })
          )}
          {error && (
            <p role="alert" className="text-miss" data-testid="create-room-error">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
