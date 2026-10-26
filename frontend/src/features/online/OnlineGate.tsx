"use client";
/**
 * Онлайн — только с аккаунтом (D13, ADR 0036). Гостю и анониму на всех онлайн-маршрутах —
 * экран «Онлайн доступен после регистрации» с входом и регистрацией; после входа —
 * возврат по безопасному `next` (тот же механизм, что у формы входа).
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Tile } from "@/shared/ui/tile";
import { useViewer } from "@/features/auth/session";
import { safeNext, withNext } from "@/features/auth/AuthForms";

export function OnlineGate({ children }: { children: ReactNode }) {
  const t = useTranslations();
  const viewer = useViewer();
  if (viewer.pending) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }
  if (viewer.kind !== "user") return <OnlineAuthRequired />;
  return <>{children}</>;
}

export function OnlineAuthRequired() {
  const t = useTranslations();
  const pathname = usePathname();
  const next = safeNext(pathname);
  return (
    <div className="mx-auto flex max-w-[640px] flex-col pt-4 sm:pt-10">
      <h1 className="sr-only">{t("online.title")}</h1>
      <Tile
        cap={t("online.title")}
        title={t("online.auth.title")}
        subtitle={t("online.auth.text")}
        data-testid="online-auth-required"
        className="gap-4 p-6"
      >
        <div className="flex flex-wrap gap-3">
          <Button asChild size="lg">
            <Link href={withNext("/login", next)} data-testid="online-login">
              {t("online.auth.login")}
            </Link>
          </Button>
          <Button asChild size="lg" variant="secondary">
            <Link href={withNext("/register", next)} data-testid="online-register">
              {t("online.auth.register")}
            </Link>
          </Button>
        </div>
        <Link
          href="/solo"
          className="text-sm text-ink-muted underline-offset-4 hover:text-ink hover:underline"
        >
          {t("online.auth.solo")}
        </Link>
      </Tile>
    </div>
  );
}
