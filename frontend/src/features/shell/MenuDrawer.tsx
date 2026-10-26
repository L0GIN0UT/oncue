"use client";
/**
 * Меню-панель: «Сегодня · музыка», «Сегодня · кино» (со статусом), «Соло», «Онлайн», «Сборники»,
 * «Профиль» и блок серии за неделю. Пунктов больше нет: остальное доступно из профиля, онлайна и шапки.
 * Данные дейликов и серии — тот же запрос `pages/home`, но только пока меню открыто.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { useTranslations } from "next-intl";
import type { DailyCard } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { useHomePage } from "@/features/daily/DailyCards";
import { Drawer } from "@/shared/ui/drawer";
import { Button } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import { Wordmark } from "@/shared/ui/wordmark";
import { weekCells, type WeekCell } from "./week";

const itemClass =
  "flex min-h-12 w-full items-center justify-between gap-3 rounded-xl px-3.5 py-2 text-left text-[17px] font-medium hover:bg-stage-raised";

function statusOf(card: DailyCard | undefined) {
  return card?.status ?? "not_started";
}

export function MenuDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useTranslations();
  const icons = useIcons();
  const pathname = usePathname();
  const viewer = useViewer();
  const { data } = useHomePage(null, { enabled: open });
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  // Переход по пункту меню закрывает панель.
  useEffect(() => {
    closeRef.current();
  }, [pathname]);

  const dailies = data?.blocks.dailies?.status === "ok" ? (data.blocks.dailies.data ?? []) : [];
  const music = dailies.find((c) => c.domain === "music");
  const film = dailies.find((c) => c.domain === "film");
  const progress = data?.blocks.progress?.status === "ok" ? data.blocks.progress.data : null;
  const streak = progress?.best_streak ?? 0;
  const playedToday = dailies.some((c) => c.status === "finished");

  const Close = icons.close;
  return (
    <Drawer open={open} onClose={onClose} aria-label={t("nav.sections")}>
      <div className="flex items-center justify-between pb-3.5 pl-1.5">
        <Wordmark className="text-xl" />
        <Button variant="icon" aria-label={t("nav.menuClose")} onClick={onClose}>
          <Close aria-hidden />
        </Button>
      </div>
      <nav aria-label={t("nav.sections")} className="flex flex-col gap-1">
        <TodayItem
          href="/"
          label={t("nav.todayMusic")}
          card={music}
          testId="nav-today-music"
          onNavigate={onClose}
        />
        <TodayItem
          href="/?domain=film"
          label={t("nav.todayFilm")}
          card={film}
          testId="nav-today-film"
          onNavigate={onClose}
        />
        <MenuLink href="/solo" label={t("nav.solo")} testId="nav-solo" onNavigate={onClose} />
        <MenuLink href="/online" label={t("nav.online")} testId="nav-online" onNavigate={onClose} />
        <MenuLink
          href="/collections"
          label={t("nav.collections")}
          testId="nav-collections"
          onNavigate={onClose}
        />
        {viewer.kind === "user" && (
          <MenuLink
            href="/profile"
            label={t("nav.profile")}
            testId="nav-profile"
            onNavigate={onClose}
          />
        )}
      </nav>
      {viewer.kind === "user" && (
        <WeekBlock streak={streak} playedToday={playedToday} loaded={progress != null} />
      )}
    </Drawer>
  );
}

function MenuLink({
  href,
  label,
  testId,
  onNavigate,
}: {
  href: string;
  label: string;
  testId: string;
  onNavigate: () => void;
}) {
  return (
    <Link href={href} data-testid={testId} onClick={onNavigate} className={itemClass}>
      {label}
    </Link>
  );
}

function TodayItem({
  href,
  label,
  card,
  testId,
  onNavigate,
}: {
  href: string;
  label: string;
  card: DailyCard | undefined;
  testId: string;
  onNavigate: () => void;
}) {
  const t = useTranslations();
  const status = statusOf(card);
  return (
    <Link href={href} data-testid={testId} onClick={onNavigate} className={itemClass}>
      <span>{label}</span>
      {card && (
        <span
          className={cn("text-[13px]", status === "in_progress" ? "text-music" : "text-ink-muted")}
        >
          {t(`nav.todayStatus.${status}`)}
        </span>
      )}
    </Link>
  );
}

function WeekBlock({
  streak,
  playedToday,
  loaded,
}: {
  streak: number;
  playedToday: boolean;
  loaded: boolean;
}) {
  const t = useTranslations();
  // Календарь зависит от «сегодня» на устройстве: на сервере блока нет (панель закрыта).
  const cells: WeekCell[] = weekCells(new Date(), streak, playedToday);
  if (!loaded) return null;
  return (
    <section
      aria-label={t("nav.week.label")}
      data-testid="menu-week"
      className="mt-[18px] flex flex-col gap-2.5 rounded-[var(--radius-control)] border-[1.5px] border-rule p-3.5"
    >
      <p className="text-[13px] text-ink-muted">{t("nav.week.streak", { days: streak })}</p>
      <ul className="flex gap-1.5">
        {cells.map((c, i) => (
          <li
            key={i}
            className={cn(
              "grid h-[30px] flex-1 place-items-center rounded-lg text-xs font-semibold",
              c.state === "played" && "bg-music text-night",
              c.state === "today" &&
                "border-[1.5px] border-dashed border-rule-strong text-ink-muted",
              c.state === "missed" && "bg-stage-raised text-ink-muted",
            )}
          >
            {c.day}
          </li>
        ))}
      </ul>
      {!playedToday && <p className="text-[13px] text-ink-muted">{t("nav.week.hint")}</p>}
    </section>
  );
}
