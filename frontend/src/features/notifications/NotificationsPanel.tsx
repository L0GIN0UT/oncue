"use client";
/**
 * Панель уведомлений под колокольчиком (макет Notifications): «Прочитать все», вкладки «Все» и
 * «Новые», первые уведомления плотными строками, «Все уведомления» и «Настройки уведомлений».
 * Данные и действия — те же, что у страницы `/notifications` (`useNotificationFeed`). Гостю и
 * анониму — приглашение войти.
 */
import Link from "next/link";
import { useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type { NotificationItem } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { ListRow } from "@/shared/ui/list-row";
import { PillTabs } from "@/shared/ui/pill-tabs";
import { useIcons } from "@/shared/ui/theme-provider";
import { useViewer } from "@/features/auth/session";
import { roomInviteHref } from "./invite";
import { useNotificationFeed } from "./NotificationsView";

const SHOWN = 7;

type Filter = "all" | "unread";

export function NotificationsPanel({ id, onNavigate }: { id: string; onNavigate: () => void }) {
  const t = useTranslations();
  const viewer = useViewer();
  const { list, items, unread, read, readAll, busy, actionError } = useNotificationFeed(
    viewer.kind === "user",
  );
  const [filter, setFilter] = useState<Filter>("all");
  const shown = (filter === "unread" ? items.filter((n) => n.read_at === null) : items).slice(
    0,
    SHOWN,
  );

  return (
    <div
      id={id}
      role="dialog"
      aria-label={t("notifications.title")}
      data-testid="notifications-panel"
      className="absolute top-[calc(100%+8px)] right-0 z-50 flex max-h-[min(560px,calc(100dvh-96px))] w-[min(400px,calc(100vw-24px))] flex-col gap-2.5 overflow-y-auto rounded-[var(--radius-ticket)] border-[1.5px] border-rule-strong bg-stage p-4 max-sm:fixed max-sm:inset-x-3 max-sm:top-[68px] max-sm:w-auto"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-display text-lg font-extrabold tracking-[-0.02em]">
          {t("notifications.title")}
        </span>
        {viewer.kind === "user" && (
          <button
            type="button"
            disabled={busy || unread === 0}
            onClick={() => void readAll()}
            data-testid="notifications-read-all"
            className="h-7 rounded-full border-[1.5px] border-rule px-3 text-xs font-semibold hover:border-rule-strong disabled:opacity-45"
          >
            {t("notifications.markAllRead")}
          </button>
        )}
      </div>

      {viewer.kind !== "user" ? (
        <div className="flex flex-col items-start gap-3 py-2">
          <p className="text-sm text-ink-muted">{t("notifications.usersOnly")}</p>
          <Link
            href="/login"
            onClick={onNavigate}
            className="rounded-full border-[1.5px] border-rule bg-stage-raised px-4 py-1.5 text-sm font-semibold"
          >
            {t("nav.login")}
          </Link>
        </div>
      ) : (
        <>
          <PillTabs
            aria-label={t("notifications.list")}
            tabs={[
              { value: "all", label: t("notifications.tabAll") },
              { value: "unread", label: t("notifications.tabUnread", { count: unread }) },
            ]}
            value={filter}
            onChange={setFilter}
          />
          {actionError && (
            <p role="alert" className="text-sm text-miss" data-testid="notifications-action-error">
              {t("notifications.actionError")}
            </p>
          )}
          {list.isPending ? (
            <p className="text-sm text-ink-muted" role="status">
              {t("common.loading")}
            </p>
          ) : list.isError && !list.data ? (
            <p role="alert" className="text-sm text-miss">
              {t("notifications.loadError")}
            </p>
          ) : shown.length === 0 ? (
            <p className="py-2 text-sm text-ink-muted">
              {filter === "unread" ? t("notifications.unreadNone") : t("notifications.empty")}
            </p>
          ) : (
            <ul className="flex flex-col gap-1" aria-label={t("notifications.list")}>
              {shown.map((n) => (
                <PanelRow
                  key={n.id}
                  item={n}
                  onRead={() => void read(n.id)}
                  onNavigate={onNavigate}
                />
              ))}
            </ul>
          )}
        </>
      )}

      <div className="mt-auto flex items-center justify-between gap-3 pt-1 text-xs text-ink-soft">
        <Link
          href="/notifications"
          onClick={onNavigate}
          data-testid="notifications-all-link"
          className="hover:text-ink"
        >
          {t("notifications.all")}
        </Link>
        <Link
          href="/notifications/settings"
          onClick={onNavigate}
          data-testid="notifications-settings-link"
          className="hover:text-ink"
        >
          {t("notifications.settingsLink")}
        </Link>
      </div>
    </div>
  );
}

function PanelRow({
  item,
  onRead,
  onNavigate,
}: {
  item: NotificationItem;
  onRead: () => void;
  onNavigate: () => void;
}) {
  const f = useFormatter();
  const t = useTranslations();
  const { bell: Bell } = useIcons();
  const isRead = item.read_at !== null;
  const invite = roomInviteHref(item);
  const body = (
    <ListRow
      lead={
        <span
          className={cn(
            "grid size-8 place-items-center rounded-full border-[1.5px] border-rule bg-stage",
            isRead ? "text-ink-muted" : "text-music",
          )}
        >
          <Bell aria-hidden className="size-4" />
        </span>
      }
      trailing={
        <time dateTime={item.created_at} className="text-[11px]">
          {f.dateTime(new Date(item.created_at), { hour: "2-digit", minute: "2-digit" })}
        </time>
      }
      className="min-h-[52px]"
    >
      <span className="flex min-w-0 flex-col">
        <span dir="auto" className={cn("truncate text-[13px]", !isRead && "font-semibold")}>
          {item.title}
        </span>
        <span dir="auto" className="truncate text-[11px] text-ink-muted">
          {item.body}
        </span>
      </span>
    </ListRow>
  );
  return (
    <li data-testid="notification-item" data-read={isRead} data-type={item.type}>
      {invite ? (
        <Link
          href={invite}
          onClick={() => {
            if (!isRead) onRead();
            onNavigate();
          }}
          data-testid="notification-join"
          aria-label={`${t("notifications.join")}: ${item.title}`}
        >
          {body}
        </Link>
      ) : isRead ? (
        body
      ) : (
        <button
          type="button"
          onClick={onRead}
          aria-label={`${t("notifications.markRead")}: ${item.title}`}
          className="block w-full text-left"
        >
          {body}
        </button>
      )}
    </li>
  );
}
