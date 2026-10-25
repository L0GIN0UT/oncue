/**
 * Роли слотов оформления на экране. Список слотов, их порядок и названия — данные (конфиг
 * progression `shop.slots`, витрина bff), код их не перечисляет. Но компоненту «ник + аватар +
 * рамка + титул» нужно знать, какой слот что значит; соответствие ключей ролям — здесь, в одном
 * месте. Слот, которого нет в конфиге, просто не рисуется.
 */
import type { AppearanceSlotCard, CosmeticCard } from "@/shared/api/types";

export const SLOT_ROLES = {
  avatar: "avatar",
  frame: "frame",
  title: "title",
  background: "background",
} as const;

export type SlotRole = keyof typeof SLOT_ROLES;

/** Достаточно полей, общих у карточки bff и `ItemView` progression. */
export type CosmeticLike = Pick<
  CosmeticCard,
  "key" | "slot" | "title" | "media_kind" | "asset_url" | "poster_url"
>;

export type AppearanceLike = {
  custom_avatar_url?: string | null;
  /** Ассет одобренной аватарки: объект жалобы `avatar` (ADR 0069). */
  custom_avatar_asset_id?: string | null;
  slots: { slot: string; item?: CosmeticLike | null }[];
};

/** Предмет, надетый в слоте с данной ролью. */
export function itemOf(appearance: AppearanceLike | null | undefined, role: SlotRole) {
  return appearance?.slots.find((s) => s.slot === SLOT_ROLES[role])?.item ?? null;
}

/** Оформление из ответа progression `/v1/appearance` (аватарка лежит рядом со слотами). */
export function toAppearanceLike(p: {
  appearance: { slots: AppearanceSlotCard[] };
  custom_avatar_url: string | null;
  custom_avatar_asset_id?: string | null;
}): AppearanceLike {
  return {
    custom_avatar_url: p.custom_avatar_url,
    custom_avatar_asset_id: p.custom_avatar_asset_id ?? null,
    slots: p.appearance.slots,
  };
}
