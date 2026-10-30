"use client";
/**
 * Подписи слотов и редкостей. Сами списки слотов и редкостей — данные (конфиг progression,
 * витрина bff), код их не перечисляет; здесь только ru-подписи по ключу из `messages/ru.json`.
 * Неизвестный ключ (новый слот или редкость из конфига) показывается как есть — экран не ломается.
 */
import { useTranslations } from "next-intl";
import { useDynamicT } from "@/shared/i18n/dynamic";

/** Подпись вкладки слота (`shop.slots.<key>`). */
export function useSlotTabLabel(): (slot: string) => string {
  const dt = useDynamicT();
  return (slot) => dt(`shop.slots.${slot}`, slot);
}

/** Единственное число названия слота (`shop.slotName.<key>`). */
export function useSlotName(): (slot: string) => string {
  const dt = useDynamicT();
  return (slot) => dt(`shop.slotName.${slot}`, slot);
}

export function useRarityLabel(): (rarity: string) => string {
  const dt = useDynamicT();
  return (rarity) => dt(`shop.rarity.${rarity}`, rarity);
}

export function useVerses(): (amount: number) => string {
  const t = useTranslations();
  return (amount) => t("shop.price", { price: amount });
}
