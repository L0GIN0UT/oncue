"use client";
/**
 * Плитка предмета витрины: квадрат с картинкой и короткой подписью состояния (цена, «куплено»,
 * «надето», уровень). Выбранная плитка получает янтарную рамку и свечение; закрытая приглушена.
 */
import { useTranslations } from "next-intl";
import type { CosmeticCard, ShopItemState } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { CosmeticImage } from "@/features/cosmetics/CosmeticImage";
import { useSlotName } from "./labels";

export type TileState = ShopItemState | "equipped";

export function ItemTile({
  item,
  state,
  selected,
  onSelect,
  testId,
}: {
  item: CosmeticCard;
  state: TileState;
  selected: boolean;
  onSelect: () => void;
  testId: string;
}) {
  const t = useTranslations();
  const slotName = useSlotName();
  const caption =
    state === "equipped"
      ? t("shop.tile.equipped")
      : state === "owned"
        ? t("shop.tile.owned")
        : state === "locked" && item.level_required != null
          ? t("shop.tile.level", { level: item.level_required })
          : item.price != null
            ? item.price
            : t("shop.free");
  const label =
    state === "locked" && item.level_required != null
      ? t("shop.unlockLevel", { level: item.level_required })
      : typeof caption === "number"
        ? t("shop.price", { price: caption })
        : caption;
  return (
    <button
      type="button"
      aria-pressed={selected}
      aria-label={`${slotName(item.slot)} ${item.title}, ${label}`}
      data-testid={testId}
      data-state={state === "equipped" ? "owned" : state}
      data-equipped={state === "equipped"}
      data-rarity={item.rarity}
      onClick={onSelect}
      className={cn(
        "flex aspect-square flex-col items-center justify-center gap-2 rounded-[var(--radius-control)] border-[1.5px] p-1 text-ink transition-colors",
        selected
          ? "border-music bg-stage shadow-[0_0_0_4px_var(--color-glow)]"
          : "border-rule hover:border-rule-strong hover:bg-stage",
        state === "locked" && "opacity-60",
      )}
    >
      <CosmeticImage item={item} decorative className="size-11 rounded-full object-cover" />
      <span aria-hidden className="tabular max-w-full truncate px-1 text-xs text-ink-muted">
        {caption}
      </span>
    </button>
  );
}
