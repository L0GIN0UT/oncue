"use client";
/** Баннер «Ограничения аккаунта» в оболочке: виден, пока есть действующие санкции. */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";
import { useSanctions } from "./use-sanctions";

export function SanctionsBanner() {
  const t = useTranslations();
  const items = useSanctions();
  if (items.length === 0) return null;
  return (
    <div
      role="region"
      aria-label={t("social.sanctions.bannerLabel")}
      data-testid="sanctions-banner"
      className="mx-auto flex w-full max-w-5xl px-4 sm:px-6"
    >
      <p className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 rounded-[var(--radius-control)] bg-stage px-4 py-3 text-sm ring-1 ring-partial">
        <TriangleAlert className="size-4 shrink-0 text-partial" aria-hidden />
        <span className="font-medium">{t("social.sanctions.banner")}</span>
        <Link
          href="/sanctions"
          className="underline underline-offset-4 hover:text-ink"
          data-testid="sanctions-link"
        >
          {t("social.sanctions.details")}
        </Link>
      </p>
    </div>
  );
}
