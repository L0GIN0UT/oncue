"use client";
/**
 * Компактная карточка прогресса на главной: уровень, полоса опыта, серия, «колесо доступно»,
 * число открытых заданий. Данные — блок `progress` bff `/pages/home` (тот же запрос, что у дейликов):
 * `null` (гость, аноним) — карточки нет; `unavailable` — короткая заглушка, остальная главная цела.
 */
import Link from "next/link";
import { useTranslations } from "next-intl";
import type { HomePage } from "@/shared/api/types";
import { useHomePage } from "@/features/daily/DailyCards";
import { XpBar } from "./XpBar";

export function HomeProgress({ initial }: { initial: HomePage | null }) {
  const t = useTranslations();
  const { data } = useHomePage(initial);
  const block = data?.blocks.progress;
  if (!block) return null;
  const card = block.status === "ok" ? block.data : null;
  return (
    <section
      aria-labelledby="home-progress-title"
      data-testid="home-progress"
      className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="home-progress-title" className="font-display text-lg font-semibold">
          {t("progression.home.title")}
        </h2>
        <Link href="/profile" className="text-sm underline-offset-4 hover:underline">
          {t("progression.home.open")}
        </Link>
      </div>
      {card ? (
        <>
          <p className="font-display text-2xl font-bold" data-testid="home-level">
            {t("progression.level.value", { level: card.level })}
          </p>
          <XpBar
            compact
            xpInLevel={card.xp_in_level}
            xpToNext={card.xp_to_next}
            level={card.level}
          />
          <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-ink-muted">
            <li data-testid="home-streak">
              {t("progression.home.streak", { days: card.best_streak })}
            </li>
            <li data-testid="home-quests">
              {t("progression.home.quests", { count: card.quests_open })}
            </li>
            <li data-testid="home-wheel">
              {card.can_spin ? t("progression.home.wheelReady") : t("progression.home.wheelDone")}
            </li>
          </ul>
        </>
      ) : (
        <p className="text-sm text-ink-muted" data-testid="home-progress-unavailable">
          {t("progression.blockUnavailable")}
        </p>
      )}
    </section>
  );
}
