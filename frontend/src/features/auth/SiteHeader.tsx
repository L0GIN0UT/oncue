"use client";
/** Шапка: знак, раздел «Соло», статус входа (личные данные — только на клиенте, D2). */
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
        {subject?.kind === "user" ? (
          <span className="rounded-md px-3 py-2 font-medium text-ink" data-testid="user-name">
            {subject.display_name ?? t("nav.player")}
          </span>
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
