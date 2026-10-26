"use client";
/**
 * Единая шапка приложения: слева кнопка меню (или кнопка процесса: «Назад», «Сдаться», «Выйти»),
 * по центру знак, справа события, уведомления и аватар. Левая и правая группы одной ширины
 * (`1fr auto 1fr`), поэтому знак стоит ровно по центру экрана.
 */
import Link from "next/link";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { HeaderEventLink } from "@/features/events/EventBanner";
import { PresenceHeartbeat } from "@/features/friends/use-presence";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { useSessionInfo } from "@/features/auth/session";
import { Avatar } from "@/shared/ui/avatar";
import { Button, buttonVariants } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import { Wordmark } from "@/shared/ui/wordmark";
import { useHeaderLeading } from "./header-leading";
import { MenuDrawer } from "./MenuDrawer";

export function AppHeader() {
  const t = useTranslations();
  const icons = useIcons();
  const { data } = useSessionInfo();
  const leading = useHeaderLeading();
  const [menuOpen, setMenuOpen] = useState(false);
  const subject = data?.subject ?? null;
  const Menu = icons.menu;
  return (
    <header className="mx-auto grid w-full max-w-[1280px] grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 py-3.5 sm:px-6">
      <div className="flex items-center justify-start">
        {leading ?? (
          <Button
            variant="icon"
            aria-label={t("nav.menu")}
            aria-expanded={menuOpen}
            aria-haspopup="dialog"
            data-testid="menu-button"
            onClick={() => setMenuOpen(true)}
          >
            <Menu aria-hidden />
          </Button>
        )}
      </div>
      <Link
        href="/"
        aria-label={t("nav.home")}
        className="rounded-lg text-[26px] leading-none sm:text-[30px]"
      >
        <Wordmark />
      </Link>
      <div className="flex items-center justify-end gap-1">
        <HeaderEventLink />
        {subject?.kind === "user" && <NotificationBell />}
        {subject?.kind === "user" && <PresenceHeartbeat />}
        {subject?.kind === "user" ? (
          <Link
            href="/profile"
            aria-label={t("nav.profile")}
            className="ml-1.5 rounded-full"
            dir="auto"
          >
            <Avatar name={subject.display_name} />
            <span className="sr-only" data-testid="user-name">
              {subject.display_name ?? t("nav.player")}
            </span>
          </Link>
        ) : (
          <Link href="/login" className={buttonVariants({ variant: "secondary", size: "sm" })}>
            {t("nav.login")}
          </Link>
        )}
      </div>
      <MenuDrawer open={menuOpen} onClose={() => setMenuOpen(false)} />
    </header>
  );
}
