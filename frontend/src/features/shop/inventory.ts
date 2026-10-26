"use client";
/**
 * Инвентарь и оформление. Данные — блоки профиля bff (`inventory`, `appearance`); надеть/снять —
 * `PUT /v1/me/appearance/{slot}` прямо в progression. Пока идёт запрос, действия заблокированы;
 * после ответа профиль перечитывается.
 */
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { AppearanceCard, InventoryEntryCard } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { fetchProfilePage, profileQueryKey } from "@/features/progression/api";
import { equipItem } from "./api";
import { equipErrorKey, type EquipError } from "./errors";

export const EQUIP_ERROR_KEYS = {
  notOwned: "shop.equipError.notOwned",
  slotMismatch: "shop.equipError.slotMismatch",
  network: "shop.equipError.network",
  other: "shop.equipError.other",
} as const satisfies Record<EquipError, string>;

/** Профиль (инвентарь и оформление) для витрины; кэш общий с экраном профиля. */
export function useProfileBlocks() {
  const query = useQuery({ queryKey: profileQueryKey, queryFn: fetchProfilePage });
  const blocks = query.data?.blocks;
  return {
    loading: query.isPending,
    inventory: blockData(blocks?.inventory)?.items ?? null,
    appearance: blockData(blocks?.appearance) as AppearanceCard | null,
  };
}

export type { InventoryEntryCard };

export function useEquip() {
  const client = useQueryClient();
  const lock = useRef(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<EquipError | null>(null);

  const run = async (slot: string, itemId: string | null) => {
    if (lock.current) return;
    lock.current = true;
    setPending(slot);
    setError(null);
    try {
      await equipItem(slot, itemId);
      await client.invalidateQueries({ queryKey: profileQueryKey });
      await client.invalidateQueries({ queryKey: ["appearance"] });
    } catch (e) {
      setError(equipErrorKey(e));
    } finally {
      lock.current = false;
      setPending(null);
    }
  };
  return { run, pending, error, clearError: () => setError(null) };
}
