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
        className="relative grid size-10 flex-none place-items-center rounded-full border-[1.5px] border-rule bg-stage text-ink-muted transition-colors hover:border-rule-strong hover:text-ink"
      >
        <Bell aria-hidden />
        {count > 0 && (
          <span
            data-testid="notification-count"
            data-count={count}
            aria-hidden
            className="tabular absolute top-1.5 right-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-music px-1 text-[10px] leading-none font-bold text-night"
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
