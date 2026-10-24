"use client";
/** Карточка предмета витрины: картинка, название, редкость, цена в Версах, уровень, состояние. */
import { useTranslations } from "next-intl";
import { useIcons } from "@/shared/ui/theme-provider";
import type { ReactNode } from "react";
import type { CosmeticCard, ShopItemState } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { CosmeticImage } from "@/features/cosmetics/CosmeticImage";
import { useRarityLabel, useVerses } from "./labels";

export function ItemCard({
  item,
  state,
  actions,
}: {
  item: CosmeticCard;
  state: ShopItemState;
  actions?: ReactNode;
}) {
  const t = useTranslations();
  const rarity = useRarityLabel();
  const verses = useVerses();
  const { locked: Lock, owned: Check } = useIcons();
  return (
    <li
      data-testid={`shop-item-${item.key}`}
      data-state={state}
      data-rarity={item.rarity}
      className={cn(
        "flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-4 ring-1 ring-rule",
        state === "locked" && "opacity-85",
      )}
    >
      <CosmeticImage item={item} className="aspect-square w-full rounded-[var(--radius-control)]" />
      <div className="flex flex-col gap-1">
        <h3 dir="auto" className="font-medium">
          {item.title}
        </h3>
        <p className="text-xs text-ink-muted">{rarity(item.rarity)}</p>
        <p className="tabular text-sm text-ink-muted">
          {item.price != null ? verses(item.price) : t("shop.free")}
          {item.level_required != null &&
            state !== "locked" &&
            ` · ${t("shop.level", { level: item.level_required })}`}
        </p>
        {state === "locked" && item.level_required != null && (
          <p className="flex items-center gap-1 text-sm text-ink-muted">
            <Lock className="size-3.5" aria-hidden />
            {t("shop.unlockLevel", { level: item.level_required })}
          </p>
        )}
        {state === "owned" && (
          <p className="flex items-center gap-1 text-sm text-exact">
            <Check className="size-3.5" aria-hidden />
            {t("shop.state.owned")}
          </p>
        )}
      </div>
      {actions}
    </li>
  );
}
