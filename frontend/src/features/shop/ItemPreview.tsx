"use client";
/**
 * Панель предпросмотра справа от сетки: выбранный предмет надет на аватар игрока, название,
 * слот и редкость, одно действие (купить, надеть, снять) или причина, почему предмета пока нет.
 */
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import type { AppearanceCard, CosmeticCard } from "@/shared/api/types";
import { Avatar, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import type { AppearanceLike } from "@/features/cosmetics/slots";
import { useRarityLabel, useSlotName } from "./labels";

/** Оформление игрока, в котором слот предмета занят самим предметом. */
export function previewAppearance(
  current: AppearanceCard | null,
  item: CosmeticCard,
): AppearanceLike {
  const slots = (current?.slots ?? []).filter((s) => s.slot !== item.slot);
  return {
    // Предмет слота «аватар» примеряется вместо загруженной картинки.
    custom_avatar_url: item.slot === "avatar" ? null : (current?.custom_avatar_url ?? null),
    slots: [...slots, { slot: item.slot, item }],
  };
}

export function ItemPreview({
  item,
  nickname,
  appearance,
  children,
}: {
  item: CosmeticCard;
  nickname: string;
  appearance: AppearanceCard | null;
  /** Действие и сообщения под описанием. */
  children: ReactNode;
}) {
  const t = useTranslations();
  const slotName = useSlotName();
  const rarity = useRarityLabel();
  const shown = previewAppearance(appearance, item);
  const title = shown.slots.find((s) => s.slot === "title")?.item?.title;
  return (
    <aside
      aria-label={t("shop.preview.label")}
      data-testid="shop-preview"
      className="flex flex-col items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-[18px] text-center"
    >
      <ProfileBackdrop appearance={shown} className="rounded-[var(--radius-control)] p-3">
        <div className="flex flex-col items-center gap-2">
          <Avatar nickname={nickname} appearance={shown} size="lg" />
          <span className="max-w-full truncate text-sm font-medium" dir="auto">
            {nickname}
          </span>
          {title && (
            <span className="max-w-full truncate text-xs text-ink-muted" dir="auto">
              {title}
            </span>
          )}
        </div>
      </ProfileBackdrop>
      <h2 dir="auto" className="font-display text-base font-medium">
        {item.title}
      </h2>
      <p className="text-[13px] leading-normal text-ink-muted">
        {t("shop.preview.about", { slot: slotName(item.slot), rarity: rarity(item.rarity) })}
      </p>
      <div className="mt-auto flex w-full flex-col gap-2">{children}</div>
    </aside>
  );
}
