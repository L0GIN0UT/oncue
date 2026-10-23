"use client";
/**
 * Шапка: знак, разделы «Соло», «Онлайн» (этап 4) и «Сборники», «Медиатека» (только пользователю, этап 3b D7),
 * статус входа (личные данные — только на клиенте, D2).
 */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Wordmark } from "@/shared/ui/wordmark";
import { useSessionInfo } from "./session";

export function SiteHeader() {
  const t = useTranslations();
  const { data } = useSessionInfo();
  const subject = data?.subject ?? null;
  return (
    <header className="mx-auto flex w-full max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
      <Link href="/" className="text-xl" aria-label={t("nav.home")}>
        <Wordmark />
      </Link>
      <nav className="flex items-center gap-1 text-sm sm:gap-3">
        <Link href="/solo" className="rounded-md px-3 py-2 text-ink-muted hover:text-ink">
          {t("nav.solo")}
        </Link>
        <Link
          href="/online"
          className="rounded-md px-3 py-2 text-ink-muted hover:text-ink"
          data-testid="nav-online"
        >
          {t("nav.online")}
        </Link>
        <Link
          href="/leaderboards"
          className="rounded-md px-3 py-2 text-ink-muted hover:text-ink"
          data-testid="nav-leaderboards"
        >
          {t("nav.leaderboards")}
        </Link>
        <Link
          href="/collections"
          className="rounded-md px-3 py-2 text-ink-muted hover:text-ink"
          data-testid="nav-collections"
        >
          {t("nav.collections")}
        </Link>
        {subject?.kind === "user" && (
          <Link
            href="/library"
            className="rounded-md px-3 py-2 text-ink-muted hover:text-ink"
            data-testid="nav-library"
          >
            {t("nav.library")}
          </Link>
        )}
        {subject?.kind === "user" && (
          <Link
            href="/me/security"
            className="rounded-md px-3 py-2 text-ink-muted hover:text-ink"
            data-testid="nav-security"
          >
            {t("nav.security")}
          </Link>
        )}
        {subject?.kind === "user" ? (
          <Link
            href="/me/collections"
            className="rounded-md px-3 py-2 font-medium text-ink hover:underline"
            data-testid="user-name"
            dir="auto"
            title={t("nav.myCollections")}
          >
            {subject.display_name ?? t("nav.player")}
          </Link>
        ) : (
          <Link
            href="/login"
            className="rounded-md px-3 py-2 font-medium text-ink ring-1 ring-rule ring-inset hover:bg-stage-raised"
          >
            {t("nav.login")}
          </Link>
        )}
      </nav>
    </header>
  );
}
