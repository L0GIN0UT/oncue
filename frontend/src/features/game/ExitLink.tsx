"use client";
/** «Выйти» в оболочке игры: возврат на главную, прогресс сессии на сервере сохраняется. */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";

export function ExitLink({ href = "/" }: { href?: string }) {
  const t = useTranslations();
  return (
    <Link
      href={href}
      data-testid="game-exit"
      className="inline-flex h-11 items-center gap-2 rounded-full border-[1.5px] border-rule bg-stage pr-4 pl-3 text-sm font-semibold text-ink transition-colors hover:border-rule-strong"
    >
      <ArrowLeft className="size-[18px]" aria-hidden />
      {t("game.exit")}
    </Link>
  );
}
