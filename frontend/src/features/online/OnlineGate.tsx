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
import { Users } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { useViewer } from "@/features/auth/session";
import { safeNext } from "@/features/auth/AuthForms";

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
    <section
      className="mt-10 flex max-w-2xl flex-col items-start gap-5 rounded-[var(--radius-ticket)] bg-stage p-7 ring-1 ring-rule sm:mt-16 sm:p-10"
      data-testid="online-auth-required"
      aria-labelledby="online-auth-title"
    >
      <span className="grid size-12 place-items-center rounded-full bg-stage-raised ring-1 ring-rule">
        <Users className="size-6 text-[var(--color-music)]" aria-hidden />
      </span>
      <h1
        id="online-auth-title"
        className="font-display text-2xl leading-tight font-bold sm:text-3xl"
      >
        {t("online.auth.title")}
      </h1>
      <p className="max-w-[52ch] text-ink-muted">{t("online.auth.text")}</p>
      <div className="flex flex-wrap gap-3">
        <Button asChild size="lg">
          <Link href={`/login?next=${encodeURIComponent(next)}`} data-testid="online-login">
            {t("online.auth.login")}
          </Link>
        </Button>
        <Button asChild size="lg" variant="secondary">
          <Link href="/register" data-testid="online-register">
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
    </section>
  );
}
