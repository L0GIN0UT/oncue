"use client";
/** Колокольчик в шапке: ссылка на `/notifications` с числом непрочитанных (aria-live). */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useIcons } from "@/shared/ui/theme-provider";
import { RoomInviteCard } from "./RoomInviteCard";
import { useUnreadCount } from "./use-unread";

const MAX_SHOWN = 99;

export function NotificationBell() {
  const t = useTranslations();
  const { bell: Bell } = useIcons();
  const count = useUnreadCount();
  return (
    <>
      <Link
        href="/notifications"
        data-testid="notification-bell"
        aria-label={t("nav.notifications")}
        className="relative rounded-md px-3 py-2 text-ink-muted hover:text-ink"
      >
        <Bell className="size-5" aria-hidden />
        {count > 0 && (
          <span
            data-testid="notification-count"
            data-count={count}
            aria-hidden
            className="tabular absolute top-0.5 right-0.5 grid min-w-5 place-items-center rounded-full bg-miss px-1 text-xs leading-5 font-semibold text-night"
          >
            {count > MAX_SHOWN ? `${MAX_SHOWN}+` : count}
          </span>
        )}
      </Link>
      <span className="sr-only" role="status" aria-live="polite" data-testid="notification-live">
        {count > 0 ? t("notifications.unread", { count }) : ""}
      </span>
      <RoomInviteCard />
    </>
  );
}
