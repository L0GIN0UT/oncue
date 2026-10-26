"use client";
/**
 * Лента дейлика: результаты друзей двумя бейджами (нота — трек, рамка — кадр). bff пока не отдаёт
 * результаты друзей по дейлику (см. handoff), поэтому показываем пустое состояние с действием.
 */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { SectionCap } from "@/shared/ui/section-cap";
import { Tile } from "@/shared/ui/tile";
import { useIcons } from "@/shared/ui/theme-provider";

export function DailyFeed({ isUser }: { isUser: boolean }) {
  const t = useTranslations();
  const icons = useIcons();
  const Plus = icons.plus;
  return (
    <Tile
      className="flex-col items-start gap-3 !py-3 sm:flex-row sm:items-center sm:gap-[18px]"
      data-testid="home-feed"
    >
      <div className="flex min-w-[130px] flex-col gap-0.5">
        <SectionCap>{t("home.feed.cap")}</SectionCap>
        <span className="text-xs text-ink-muted">{t("home.feed.legend")}</span>
      </div>
      <p className="min-w-0 flex-1 text-[13px] text-ink-muted" data-testid="home-feed-empty">
        {t("home.feed.empty")}
      </p>
      <Link
        href={isUser ? "/friends" : "/login"}
        data-testid="home-feed-action"
        className="inline-flex h-9 flex-none items-center gap-2 rounded-full border-[1.5px] border-rule bg-stage px-3.5 text-[13px] font-semibold whitespace-nowrap transition-colors hover:border-rule-strong"
      >
        <Plus aria-hidden className="size-3.5" />
        {isUser ? t("home.feed.addFriends") : t("home.feed.login")}
      </Link>
    </Tile>
  );
}
