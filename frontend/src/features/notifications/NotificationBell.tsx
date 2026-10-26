"use client";
/**
 * Колокольчик в шапке: кнопка с числом непрочитанных (aria-live) открывает панель уведомлений
 * под собой (макет Notifications). Панель закрывается по Escape, клику снаружи и при переходе на
 * другую страницу; полный список — на `/notifications`.
 */
import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useIcons } from "@/shared/ui/theme-provider";
import { NotificationsPanel } from "./NotificationsPanel";
import { RoomInviteCard } from "./RoomInviteCard";
import { useUnreadCount } from "./use-unread";

const MAX_SHOWN = 99;

export function NotificationBell() {
  const t = useTranslations();
  const { bell: Bell } = useIcons();
  const count = useUnreadCount();
  const pathname = usePathname();
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const [lastPath, setLastPath] = useState(pathname);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  // Переход на другую страницу закрывает панель.
  if (lastPath !== pathname) {
    setLastPath(pathname);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <>
      <div ref={root} className="relative">
        <button
          ref={button}
          type="button"
          data-testid="notification-bell"
          aria-label={t("nav.notifications")}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={open ? panelId : undefined}
          onClick={() => setOpen((v) => !v)}
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
        </button>
        {open && <NotificationsPanel id={panelId} onNavigate={() => setOpen(false)} />}
      </div>
      <span className="sr-only" role="status" aria-live="polite" data-testid="notification-live">
        {count > 0 ? t("notifications.unread", { count }) : ""}
      </span>
      <RoomInviteCard />
    </>
  );
}
