"use client";
/**
 * Оболочка игры вместо шапки сайта (маршруты `/play/*`, `/match/*`, `/room/*`): слева выход и
 * громкость, по центру знак и подпись режима, справа счёт. Содержимого не знает: экраны игры
 * передают слоты пропсами. Пока экран не передал `exit`, слева кнопка из слота `HeaderLeading`
 * («Сдаться», «Выйти»), чтобы действующие экраны не потеряли выход.
 */
import Link from "next/link";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { SectionCap } from "@/shared/ui/section-cap";
import { Wordmark } from "@/shared/ui/wordmark";
import { useHeaderLeading } from "./header-leading";

export type GameShellProps = {
  /** Кнопка выхода слева. */
  exit?: ReactNode;
  /** Регулятор громкости рядом с выходом. */
  volume?: ReactNode;
  /** Название режима под знаком («Дейлик · музыка»). */
  modeLabel?: ReactNode;
  /** Счёт справа («0 / 30 очки»). */
  score?: ReactNode;
};

export function GameShell({ exit, volume, modeLabel, score }: GameShellProps) {
  const t = useTranslations();
  const slot = useHeaderLeading();
  return (
    <header
      data-testid="game-shell"
      className="mx-auto grid w-full max-w-[1280px] grid-cols-[1fr_auto_1fr] items-center gap-2 px-4 py-3.5 sm:px-6"
    >
      <div className="flex items-center justify-start gap-2">
        {exit ?? slot}
        {volume}
      </div>
      <div className="flex flex-col items-center gap-1">
        <Link href="/" aria-label={t("nav.logo")} className="rounded-lg text-xl leading-none">
          <Wordmark />
        </Link>
        {modeLabel != null && <SectionCap data-testid="game-mode">{modeLabel}</SectionCap>}
      </div>
      <div className="flex items-center justify-end text-sm font-semibold">{score}</div>
    </header>
  );
}
