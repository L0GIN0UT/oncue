"use client";
/** Подписи рейтинга из данных сервера: звание, ladder, формат (названия — только через i18n). */
import { useTranslations } from "next-intl";
import { useDynamicT } from "@/shared/i18n/dynamic";
import type { TierCard } from "@/shared/api/types";

export type { RatingCard, SeasonCard, TierCard } from "@/shared/api/types";

/** «Бронза 2»; высшее звание без ступени — просто название. */
export function useTierLabel(): (tier: TierCard) => string {
  const t = useTranslations();
  const dt = useDynamicT();
  return (tier) => {
    // Незнакомое звание (ключ не переведён) — нейтральное слово, а не сырой ключ семейства.
    const title = dt(tier.title_key, t("rating.tierUnknown"));
    return tier.step ? t("rating.tierStep", { title, step: tier.step }) : title;
  };
}

/** Название ladder-а по ключу (запасной вариант — сам ключ). */
export function useLadderLabel(): (ladder: string) => string {
  const dt = useDynamicT();
  return (ladder) => dt(`rating.ladder.${ladder}`, ladder);
}

/** Название формата очереди (`classic`/`lite`). */
export function useFormatLabel(): (format: string) => string {
  const dt = useDynamicT();
  return (format) => dt(`rating.format.${format}`, format);
}
