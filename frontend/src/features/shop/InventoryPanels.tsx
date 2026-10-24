"use client";
/**
 * Вкладки «Инвентарь» и «Оформление». Данные — блоки профиля bff (`inventory`, `appearance`);
 * надеть/снять — `PUT /v1/me/appearance/{slot}` прямо в progression. Слоты и их порядок берутся
 * из витрины (данные), предметы слота — из инвентаря игрока. Пока идёт запрос, элемент
 * заблокирован и показывает выбранное значение; после ответа профиль перечитывается.
 */
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import type { InventoryEntryCard } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { CosmeticImage } from "@/features/cosmetics/CosmeticImage";
import { fetchProfilePage, profileQueryKey } from "@/features/progression/api";
import { equipItem } from "./api";
import { equipErrorKey, type EquipError } from "./errors";
import { useRarityLabel, useSlotName, useSourceLabel } from "./labels";

const EQUIP_ERROR_KEYS = {
  notOwned: "shop.equipError.notOwned",
  slotMismatch: "shop.equipError.slotMismatch",
  network: "shop.equipError.network",
  other: "shop.equipError.other",
} as const satisfies Record<EquipError, string>;

/** Профиль (инвентарь и оформление) для вкладок; грузится, пока вкладка открыта. */
function useProfileBlocks() {
  const query = useQuery({ queryKey: profileQueryKey, queryFn: fetchProfilePage });
  const blocks = query.data?.blocks;
  return {
    loading: query.isPending,
    inventory: blockData(blocks?.inventory)?.items ?? null,
  };
}

function useEquip() {
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
  return { run, pending, error };
}

function EquipFeedback({ error }: { error: EquipError | null }) {
  const t = useTranslations();
  return (
    <div aria-live="polite" data-testid="equip-feedback">
      {error && (
        <p role="alert" className="text-sm text-miss">
          {t(EQUIP_ERROR_KEYS[error])}
        </p>
      )}
    </div>
  );
}

function Unavailable({ testId }: { testId: string }) {
  const t = useTranslations();
  return (
    <p className="text-ink-muted" data-testid={testId}>
      {t("shop.blockUnavailable")}
    </p>
  );
}

export function InventoryPanel() {
  const t = useTranslations();
  const f = useFormatter();
  const { loading, inventory } = useProfileBlocks();
  const equip = useEquip();
  const rarity = useRarityLabel();
  const source = useSourceLabel();
  const slotName = useSlotName();

  if (loading)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (!inventory) return <Unavailable testId="inventory-unavailable" />;
  if (!inventory.length)
    return (
      <p className="text-ink-muted" data-testid="inventory-empty">
        {t("shop.inventory.empty")}
      </p>
    );
  return (
    <div className="flex flex-col gap-3">
      <EquipFeedback error={equip.error} />
      <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {inventory.map((e: InventoryEntryCard) => (
          <li
            key={e.item.id}
            data-testid={`inventory-item-${e.item.key}`}
            data-equipped={e.equipped}
            className={cn(
              "flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-4 ring-1 ring-rule",
              e.equipped && "ring-2 ring-[var(--accent,var(--color-music))]",
            )}
          >
            <CosmeticImage
              item={e.item}
              className="aspect-square w-full rounded-[var(--radius-control)]"
            />
            <div className="flex flex-col gap-0.5">
              <h3 dir="auto" className="font-medium">
                {e.item.title}
              </h3>
              <p className="text-xs text-ink-muted">
                {slotName(e.item.slot)} · {rarity(e.item.rarity)} · {source(e.source)}
              </p>
              {e.expires_at && (
                <p className="text-xs text-ink-muted">
                  {t("shop.inventory.expires", {
                    date: f.dateTime(new Date(e.expires_at), { dateStyle: "long" }),
                  })}
                </p>
              )}
              {e.equipped && <p className="text-sm text-exact">{t("shop.inventory.equipped")}</p>}
            </div>
            <Button
              size="sm"
              variant={e.equipped ? "secondary" : "primary"}
              disabled={equip.pending !== null}
              data-testid={`equip-item-${e.item.key}`}
              onClick={() => void equip.run(e.item.slot, e.equipped ? null : e.item.id)}
            >
              {e.equipped ? t("shop.inventory.unequip") : t("shop.inventory.equip")}
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AppearancePanel({ slots }: { slots: string[] }) {
  const t = useTranslations();
  const { loading, inventory } = useProfileBlocks();
  const equip = useEquip();
  const slotName = useSlotName();
  const [choice, setChoice] = useState<{ slot: string; value: string } | null>(null);

  if (loading)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (!inventory) return <Unavailable testId="inventory-unavailable" />;

  return (
    <div className="flex flex-col gap-4">
      <p className="text-ink-muted">{t("shop.appearance.lead")}</p>
      <EquipFeedback error={equip.error} />
      <ul className="grid gap-4 sm:grid-cols-2">
        {slots.map((slot) => {
          const own = inventory.filter((e) => e.item.slot === slot);
          const current = own.find((e) => e.equipped)?.item.id ?? "";
          const value = choice?.slot === slot && equip.pending === slot ? choice.value : current;
          return (
            <li
              key={slot}
              data-testid={`appearance-slot-${slot}`}
              className="flex flex-col gap-2 rounded-[var(--radius-ticket)] bg-stage p-4 ring-1 ring-rule"
            >
              <label htmlFor={`equip-${slot}`} className="text-sm font-medium">
                {slotName(slot)}
              </label>
              <select
                id={`equip-${slot}`}
                data-testid={`equip-${slot}`}
                value={value}
                disabled={equip.pending !== null}
                onChange={(e) => {
                  setChoice({ slot, value: e.target.value });
                  void equip.run(slot, e.target.value || null);
                }}
                className="h-11 rounded-[var(--radius-control)] bg-night px-3 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-music"
              >
                <option value="">{t("shop.appearance.empty")}</option>
                {own.map((e) => (
                  <option key={e.item.id} value={e.item.id}>
                    {e.item.title}
                  </option>
                ))}
              </select>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
