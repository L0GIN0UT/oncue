"use client";
/**
 * Друзья `/friends` (этап 5c): список со статусами, заявки (входящие и исходящие), добавление по
 * нику или коду, блокировки и приватность. Всё — прямо в social (ADR 0017). Только пользователю:
 * аноним — вход, гость — регистрация. Статусы освежаются раз в период (`FRIENDS_REFRESH_MS`),
 * истина о присутствии — на сервере.
 */
import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { Avatar } from "@/shared/ui/avatar";
import { Button } from "@/shared/ui/button";
import { useViewer } from "@/features/auth/session";
import { ProfileGate } from "@/features/progression/ProfileView";
import { TabPanel, Tabs, type TabItem } from "@/shared/ui/tabs";
import { AddFriendPanel } from "./AddFriendPanel";
import { PrivacyPanel, BlocksPanel } from "./PrivacyBlocks";
import { ErrorLine } from "./ErrorLine";
import { StatusDot } from "./StatusDot";
import type { TabId } from "./tabs";
import { FRIENDS_REFRESH_MS } from "./config";
import { useSocialAction } from "./use-action";
import {
  acceptRequest,
  cancelRequest,
  declineRequest,
  fetchFriends,
  fetchRequests,
  friendsKeys,
  removeFriend,
} from "./api";

export function FriendsView({ initialTab = "friends" }: { initialTab?: TabId }) {
  const t = useTranslations();
  const viewer = useViewer();
  const [tab, setTab] = useState<TabId>(initialTab);
  const requests = useQuery({
    queryKey: friendsKeys.requests,
    queryFn: fetchRequests,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;

  const incoming = requests.data?.incoming.length ?? 0;
  const tabs: TabItem<TabId>[] = [
    {
      value: "friends",
      label: <span data-testid="friends-tab-friends">{t("social.tabs.friends")}</span>,
    },
    {
      value: "requests",
      label: (
        <span data-testid="friends-tab-requests">
          {incoming
            ? t("social.tabs.requestsCount", { count: incoming })
            : t("social.tabs.requests")}
        </span>
      ),
    },
    { value: "add", label: <span data-testid="friends-tab-add">{t("social.tabs.add")}</span> },
    {
      value: "blocks",
      label: <span data-testid="friends-tab-blocks">{t("social.tabs.blocks")}</span>,
    },
    {
      value: "privacy",
      label: <span data-testid="friends-tab-privacy">{t("social.tabs.privacy")}</span>,
    },
  ];

  return (
    <div className="flex flex-col gap-5 pt-2">
      <header className="flex flex-col gap-2 px-0.5">
        <h1 className="font-display text-[28px] leading-none font-extrabold">
          {t("social.title")}
        </h1>
        <p className="text-sm text-ink-muted">{t("social.lead")}</p>
      </header>
      <Tabs
        tabs={tabs}
        value={tab}
        onChange={setTab}
        idBase="friends"
        aria-label={t("social.tabs.label")}
      />
      <TabPanel idBase="friends" value="friends" active={tab === "friends"}>
        <FriendsList />
      </TabPanel>
      <TabPanel idBase="friends" value="requests" active={tab === "requests"}>
        <RequestsPanel />
      </TabPanel>
      <TabPanel idBase="friends" value="add" active={tab === "add"}>
        <AddFriendPanel />
      </TabPanel>
      <TabPanel idBase="friends" value="blocks" active={tab === "blocks"}>
        <BlocksPanel />
      </TabPanel>
      <TabPanel idBase="friends" value="privacy" active={tab === "privacy"}>
        <PrivacyPanel />
      </TabPanel>
    </div>
  );
}

/** Список друзей со статусами; используется на экране друзей и во вкладке профиля. */
export function FriendsList() {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const [confirming, setConfirming] = useState<string | null>(null);
  const list = useQuery({
    queryKey: friendsKeys.list,
    queryFn: fetchFriends,
    refetchInterval: FRIENDS_REFRESH_MS,
    refetchIntervalInBackground: false,
  });

  if (list.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (list.isError)
    return (
      <p role="alert" className="text-miss">
        {t("social.loadError")}
      </p>
    );
  const items = list.data.items;
  if (!items.length)
    return (
      <p className="text-ink-muted" data-testid="friends-empty">
        {t("social.friends.empty")}
      </p>
    );

  const remove = async (userId: string) => {
    const r = await action.run(`remove:${userId}`, () => removeFriend(userId));
    if (r.ok) {
      setConfirming(null);
      await client.invalidateQueries({ queryKey: friendsKeys.list });
    }
  };

  return (
    <>
      <ul className="flex flex-col gap-2" aria-label={t("social.friends.list")}>
        {items.map((f) => (
          <li
            key={f.user_id}
            data-testid={`friend-${f.nickname}`}
            data-status={f.status}
            className="flex min-h-14 flex-wrap items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 py-2"
          >
            <Avatar name={f.nickname} size="sm" />
            <Link
              href={`/players/${f.user_id}`}
              dir="auto"
              className="min-w-32 flex-1 font-medium underline-offset-4 hover:underline"
            >
              {f.nickname}
            </Link>
            <StatusDot status={f.status} />
            {confirming === f.user_id ? (
              <span className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={action.busy}
                  onClick={() => void remove(f.user_id)}
                  data-testid="friend-remove-confirm"
                >
                  {t("social.friends.removeConfirm")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                  {t("social.cancel")}
                </Button>
              </span>
            ) : (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setConfirming(f.user_id)}
                aria-label={t("social.friends.removeLabel", { name: f.nickname })}
                data-testid="friend-remove"
              >
                {t("social.friends.remove")}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <ErrorLine text={action.error} />
    </>
  );
}

function RequestsPanel() {
  const t = useTranslations();
  const f = useFormatter();
  const client = useQueryClient();
  const action = useSocialAction();
  const query = useQuery({ queryKey: friendsKeys.requests, queryFn: fetchRequests });

  if (query.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (query.isError)
    return (
      <p role="alert" className="text-miss">
        {t("social.loadError")}
      </p>
    );

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: friendsKeys.requests }),
      client.invalidateQueries({ queryKey: friendsKeys.list }),
    ]);
  };
  const act = async (scope: string, fn: (key: string) => Promise<void>) => {
    const r = await action.run(scope, fn);
    if (r.ok) await refresh();
  };

  const { incoming, outgoing } = query.data;
  const date = (iso: string) => f.dateTime(new Date(iso), { dateStyle: "medium" });
  return (
    <div className="flex flex-col gap-8">
      <section aria-labelledby="requests-in" className="flex flex-col gap-3">
        <h2 id="requests-in" className="font-semibold">
          {t("social.requests.incoming")}
        </h2>
        {incoming.length === 0 ? (
          <p className="text-ink-muted">{t("social.requests.incomingEmpty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {incoming.map((r) => (
              <li
                key={r.id}
                data-testid={`request-in-${r.nickname}`}
                className="flex min-h-14 flex-wrap items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 py-2"
              >
                <span dir="auto" className="min-w-32 flex-1 font-medium">
                  {r.nickname}
                </span>
                <time dateTime={r.created_at} className="text-xs text-ink-faint">
                  {date(r.created_at)}
                </time>
                <Button
                  size="sm"
                  disabled={action.busy}
                  onClick={() => void act(`accept:${r.id}`, (key) => acceptRequest(r.id, key))}
                  data-testid="request-accept"
                >
                  {t("social.requests.accept")}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={action.busy}
                  onClick={() => void act(`decline:${r.id}`, (key) => declineRequest(r.id, key))}
                  data-testid="request-decline"
                >
                  {t("social.requests.decline")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="requests-out" className="flex flex-col gap-3">
        <h2 id="requests-out" className="font-semibold">
          {t("social.requests.outgoing")}
        </h2>
        {outgoing.length === 0 ? (
          <p className="text-ink-muted">{t("social.requests.outgoingEmpty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {outgoing.map((r) => (
              <li
                key={r.id}
                data-testid={`request-out-${r.nickname}`}
                className="flex min-h-14 flex-wrap items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 py-2"
              >
                <span dir="auto" className="min-w-32 flex-1 font-medium">
                  {r.nickname}
                </span>
                <time dateTime={r.created_at} className="text-xs text-ink-faint">
                  {date(r.created_at)}
                </time>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={action.busy}
                  onClick={() => void act(`cancel:${r.id}`, () => cancelRequest(r.id))}
                  data-testid="request-cancel"
                >
                  {t("social.requests.cancel")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <ErrorLine text={action.error} />
    </div>
  );
}
