"use client";
/**
 * Шапка сайта по макету v3 (Home): слева знак, по центру «Главная / Соло / Онлайн / Сборники»
 * (активный пункт янтарный), справа колокольчик, баланс Версов, лидеры и профиль. На игровых
 * маршрутах вместо неё — `GameShell`. На узком экране навигация уходит второй строкой.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { useSessionInfo } from "@/features/auth/session";
import { NotificationBell } from "@/features/notifications/NotificationBell";
import { fetchProfilePage, profileQueryKey } from "@/features/progression/api";
import { blockData } from "@/shared/api/blocks";
import { cn } from "@/shared/lib/cn";
import { Avatar } from "@/shared/ui/avatar";
import { buttonVariants } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import { Wordmark } from "@/shared/ui/wordmark";
import { activeNav, NAV_ITEMS } from "./routes";

const PILL =
  "inline-flex h-10 flex-none items-center gap-2 rounded-full border-[1.5px] border-rule bg-stage px-3.5 text-sm leading-none font-semibold whitespace-nowrap transition-colors hover:border-rule-strong";

export function SiteHeader() {
  const t = useTranslations();
  const f = useFormatter();
  const icons = useIcons();
  const pathname = usePathname();
  const { data } = useSessionInfo();
  const subject = data?.subject ?? null;
  const isUser = subject?.kind === "user";
  const active = activeNav(pathname);
  // Баланс — из профиля (запрос общий с профилем и магазином, покупка его инвалидирует).
  const profile = useQuery({
    queryKey: profileQueryKey,
    queryFn: fetchProfilePage,
    enabled: isUser,
    staleTime: 30_000,
  });
  const balance = blockData(profile.data?.blocks.wallet)?.total ?? null;
  const Note = icons.music;
  const Verses = icons.verses;
  const Leaders = icons.leaders;

  return (
    <header className="mx-auto flex w-full max-w-[1280px] flex-wrap items-center gap-x-6 gap-y-3 px-4 pt-3.5 sm:px-6 md:flex-nowrap">
      <Link
        href="/"
        aria-label={t("nav.logo")}
        data-testid="nav-logo"
        className="flex min-w-0 items-center gap-2.5 md:flex-1"
      >
        <span className="grid size-9 flex-none place-items-center rounded-[10px] bg-music text-night">
          <Note aria-hidden className="size-5" />
        </span>
        <Wordmark className="text-2xl leading-none" />
      </Link>

      <nav
        aria-label={t("nav.sections")}
        className="order-last flex w-full items-center justify-between gap-1 md:order-none md:w-auto md:justify-center md:gap-2"
      >
        {NAV_ITEMS.map((item, i) => (
          <span key={item.key} className="flex items-center gap-1 md:gap-2">
            {i > 0 && (
              <span aria-hidden className="text-rule">
                /
              </span>
            )}
            <Link
              href={item.href}
              data-testid={`nav-${item.key}`}
              aria-current={active === item.key ? "page" : undefined}
              className={cn(
                "rounded-[10px] px-2 py-2 font-display text-sm font-extrabold tracking-[-0.02em] whitespace-nowrap transition-colors sm:text-base md:px-2.5 md:text-lg",
                active === item.key ? "text-music" : "text-ink-faint hover:text-ink",
              )}
            >
              {t(`nav.${item.key}`)}
            </Link>
          </span>
        ))}
      </nav>

      <div className="ml-auto flex min-w-0 items-center justify-end gap-2 md:ml-0 md:flex-1">
        {isUser ? (
          <>
            <NotificationBell />
            <Link
              href="/shop"
              data-testid="nav-verses"
              aria-label={
                balance != null
                  ? t("nav.versesLabel", { balance: f.number(balance) })
                  : t("nav.shop")
              }
              className={PILL}
            >
              <Verses aria-hidden className="size-4 text-music" />
              <span className="tabular">{balance != null ? f.number(balance) : "—"}</span>
            </Link>
            <Link
              href="/leaderboards"
              data-testid="nav-leaders"
              aria-label={t("nav.leaders")}
              className="grid size-10 flex-none place-items-center rounded-full border-[1.5px] border-rule bg-stage text-ink-muted transition-colors hover:border-rule-strong hover:text-ink"
            >
              <Leaders aria-hidden className="size-[22px]" />
            </Link>
            <Link
              href="/profile"
              data-testid="nav-profile"
              aria-label={t("nav.profile")}
              className={cn(PILL, "min-w-0 pl-1")}
              dir="auto"
            >
              <Avatar name={subject.display_name} size="sm" className="border-2 border-music" />
              <span className="max-w-32 truncate max-sm:sr-only" data-testid="user-name">
                {subject.display_name ?? t("nav.player")}
              </span>
            </Link>
          </>
        ) : (
          <Link href="/login" className={buttonVariants({ variant: "secondary", size: "sm" })}>
            {t("nav.login")}
          </Link>
        )}
      </div>
    </header>
  );
}
