/** Значок звания рейтинга: сталь, бронза, серебро, золото, алмаз (цвета `--color-tier-*`). */
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

export const TIER_KEYS = ["steel", "bronze", "silver", "gold", "diamond"] as const;
export type TierKey = (typeof TIER_KEYS)[number];

const BG: Record<TierKey, string> = {
  steel: "bg-tier-steel",
  bronze: "bg-tier-bronze",
  silver: "bg-tier-silver",
  gold: "bg-tier-gold",
  diamond: "bg-tier-diamond",
};

/** Семейство звания из rating (`TierCard.family`) → цвет значка; незнакомое — сталь. */
const FAMILY: Record<string, TierKey> = {
  iron: "steel",
  bronze: "bronze",
  silver: "silver",
  gold: "gold",
  platinum: "silver",
  diamond: "diamond",
  cinephile: "diamond",
  melomaniac: "diamond",
};

export function tierKeyOf(family: string | null | undefined): TierKey {
  return (family && FAMILY[family]) || "steel";
}

export function TierBadge({
  tier,
  className,
  children,
}: {
  tier: TierKey;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      data-tier={tier}
      className={cn(
        "inline-flex h-5 items-center rounded-md px-2 text-[10px] font-bold tracking-[0.06em] whitespace-nowrap text-night uppercase",
        BG[tier],
        className,
      )}
    >
      {children}
    </span>
  );
}
