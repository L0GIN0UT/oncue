"use client";
/**
 * Список уведомлений `/notifications`: новые первыми, курсорная подгрузка, «прочитано» и
 * «прочитать все» (POST в notifications). Тексты уведомлений собраны сервером по шаблону и
 * выводятся только как текст (React экранирует). Только пользователю; гость и аноним —
 * приглашение. Новые по realtime попадают в открытый список (см. `use-unread`).
 */
import { useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { NotificationItem } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { ProfileGate } from "@/features/progression/ProfileView";
import { roomInviteHref } from "./invite";
import { unreadIn, withAllRead, withRead, type ListData } from "./cache";
import {
  fetchNotifications,
  fetchUnreadCount,
  listQueryKey,
  markAllRead,
  markRead,
  unreadQueryKey,
} from "./api";

export function NotificationsView() {
  const t = useTranslations();
  const viewer = useViewer();
  const client = useQueryClient();
  const [actionError, setActionError] = useState(false);
  const [busy, setBusy] = useState(false);

  const list = useInfiniteQuery({
    queryKey: listQueryKey,
    queryFn: ({ pageParam }) => fetchNotifications(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: viewer.kind === "user",
  });

  // Общий кэш с колокольчиком: точное число на сервере (в списке загружена только часть).
  const total = useQuery({
    queryKey: unreadQueryKey,
    queryFn: fetchUnreadCount,
    enabled: viewer.kind === "user",
    retry: false,
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;

  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const loadedUnread = list.data ? unreadIn(list.data) : 0;
  const unread = Math.max(total.data ?? 0, loadedUnread);

  const patch = (fn: (d: ListData) => ListData) =>
    client.setQueryData<ListData>(listQueryKey, (d) => (d ? fn(d) : d));

  const read = async (id: string) => {
    setActionError(false);
    try {
      await markRead(id);
      patch((d) => withRead(d, id, new Date().toISOString()));
      await client.invalidateQueries({ queryKey: unreadQueryKey });
    } catch {
      setActionError(true);
    }
  };

  const readAll = async () => {
    setBusy(true);
    setActionError(false);
    try {
      await markAllRead();
      patch((d) => withAllRead(d, new Date().toISOString()));
      client.setQueryData<number>(unreadQueryKey, 0);
    } catch {
      setActionError(true);
    } finally {
      setBusy(false);
    }
  };

  const unauthorized = isApiError(list.error) && list.error.status === 401;

  return (
    <div className="flex flex-col gap-5 pt-2">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-[28px] leading-none font-extrabold">
            {t("notifications.title")}
          </h1>
          <p className="max-w-[60ch] text-ink-muted">{t("notifications.lead")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={busy || unread === 0}
            onClick={() => void readAll()}
            data-testid="notifications-read-all"
          >
            {t("notifications.markAllRead")}
          </Button>
          <Button asChild variant="ghost" size="sm">
            <Link href="/notifications/settings" data-testid="notifications-settings-link">
              {t("notifications.settingsLink")}
            </Link>
          </Button>
        </div>
      </header>

      <p
        className="tabular text-sm text-ink-muted"
        aria-live="polite"
        data-testid="notifications-unread"
      >
        {unread > 0 ? t("notifications.unread", { count: unread }) : t("notifications.unreadNone")}
      </p>

      {actionError && (
        <p role="alert" className="text-sm text-miss" data-testid="notifications-action-error">
          {t("notifications.actionError")}
        </p>
      )}

      {list.isPending ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : list.isError && !list.data ? (
        <p role="alert" className="text-miss">
          {unauthorized ? t("notifications.usersOnly") : t("notifications.loadError")}
        </p>
      ) : items.length === 0 ? (
        <p className="text-ink-muted">{t("notifications.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-2.5" aria-label={t("notifications.list")}>
          {items.map((n) => (
            <Row key={n.id} item={n} onRead={() => void read(n.id)} />
          ))}
        </ul>
      )}

      {list.hasNextPage && (
        <Button
          variant="secondary"
          className="self-center"
          disabled={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          {t("notifications.loadMore")}
        </Button>
      )}
    </div>
  );
}

function Row({ item, onRead }: { item: NotificationItem; onRead: () => void }) {
  const t = useTranslations();
  const f = useFormatter();
  const isRead = item.read_at !== null;
  const invite = roomInviteHref(item);
  return (
    <li
      data-testid="notification-item"
      data-read={isRead}
      data-type={item.type}
      className={cn(
        "flex flex-col gap-2 rounded-[var(--radius-control)] border-[1.5px] border-rule px-4 py-3 sm:flex-row sm:items-start sm:justify-between",
        isRead ? "bg-transparent" : "bg-stage",
      )}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <h2 dir="auto" className={cn("break-words", isRead ? "font-normal" : "font-semibold")}>
          {!isRead && (
            <span
              aria-hidden
              className="mr-2 inline-block size-2 rounded-full bg-music align-middle"
            />
          )}
          {item.title}
        </h2>
        <p dir="auto" className="text-sm break-words text-ink-muted">
          {item.body}
        </p>
        <time dateTime={item.created_at} className="text-xs text-ink-faint">
          {f.dateTime(new Date(item.created_at), { dateStyle: "medium", timeStyle: "short" })}
        </time>
      </div>
      <div className="flex flex-wrap items-start gap-2">
        {invite && (
          <Button asChild size="sm">
            <Link
              href={invite}
              onClick={isRead ? undefined : onRead}
              data-testid="notification-join"
              aria-label={`${t("notifications.join")}: ${item.title}`}
            >
              {t("notifications.join")}
            </Link>
          </Button>
        )}
        {!isRead && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRead}
            aria-label={`${t("notifications.markRead")}: ${item.title}`}
            className="self-start"
          >
            {t("notifications.markRead")}
          </Button>
        )}
      </div>
    </li>
  );
}
