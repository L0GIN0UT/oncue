"use client";
/** Кнопка выхода в оболочке игры: пилюля со стрелкой; по умолчанию «Выйти» на главную. */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";

export function ExitLink({ href = "/", label }: { href?: string; label?: string }) {
  const t = useTranslations();
  const text = label ?? t("game.exit");
  // На телефоне — круглая кнопка со стрелкой: шапка игры не вмещает подпись рядом со звуком и счётом.
  return (
    <Link
      href={href}
      aria-label={text}
      data-testid="game-exit"
      className="inline-flex size-11 items-center justify-center gap-2 rounded-full border-[1.5px] border-rule bg-stage text-sm font-semibold text-ink transition-colors hover:border-rule-strong sm:w-auto sm:pr-4 sm:pl-3"
    >
      <ArrowLeft className="size-[18px]" aria-hidden />
      <span className="hidden sm:inline">{text}</span>
    </Link>
  );
}
