"use client";
/**
 * Магазин `/shop` (шире основной колонки, 760 px): чипы слотов (порядок и состав — из данных bff,
 * код ключей не знает) и «Мои», сетка плиток, справа предпросмотр выбранного предмета с одним
 * действием, внизу промокод. Покупка — `POST /v1/shop/items/{id}/purchase` прямо в progression:
 * один `Idempotency-Key` на один показ подтверждения (повтор после сбоя — тот же ключ, новый
 * показ — новый), цена и условия проверяются сервером. Баланс и состояние плитки обновляются из
 * ответа. Надеть и снять — на панели предпросмотра у купленного предмета.
 */
import { useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { blockData } from "@/shared/api/blocks";
import { isApiError } from "@/shared/api/errors";
import type { ShopCatalogCard, ShopEntryCard, ShopPage } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { Chip } from "@/shared/ui/chip";
import { Modal } from "@/shared/ui/modal";
import { useIcons } from "@/shared/ui/theme-provider";
import { useSessionInfo, useViewer } from "@/features/auth/session";
import { PromoBlock } from "@/features/progression/PromoBlock";
import { profileQueryKey } from "@/features/progression/api";
import { gateOf, ProfileGate } from "@/features/progression/ProfileView";
import { HeaderLeading } from "@/features/shell/header-leading";
import { fetchShopPage, newIdempotencyKey, purchaseItem, shopQueryKey } from "./api";
import { purchaseErrorKey, type PurchaseError } from "./errors";
import { EQUIP_ERROR_KEYS, useEquip, useProfileBlocks } from "./inventory";
import { ItemPreview } from "./ItemPreview";
import { ItemTile, type TileState } from "./ItemTile";
import { useSlotTabLabel, useVerses } from "./labels";

const PURCHASE_ERROR_KEYS = {
  insufficientFunds: "shop.error.insufficientFunds",
  alreadyOwned: "shop.error.alreadyOwned",
  notAvailable: "shop.error.notAvailable",
  notFound: "shop.error.notFound",
  auth: "shop.error.auth",
  rateLimited: "shop.error.rateLimited",
  unavailable: "shop.error.unavailable",
  network: "shop.error.network",
  other: "shop.error.other",
} as const satisfies Record<PurchaseError, string>;

const MINE = "mine";
const slotTab = (slot: string) => `slot:${slot}`;

/** Подставить результат покупки в кэш витрины: предмет куплен, баланс из ответа. */
function markOwned(page: ShopPage, itemId: string, balance: number | null): ShopPage {
  const catalog = page.blocks.catalog;
  if (catalog.status !== "ok" || !catalog.data) return page;
  const data: ShopCatalogCard = {
    ...catalog.data,
    balance: balance ?? catalog.data.balance,
    items: catalog.data.items.map((e) =>
      e.item.id === itemId ? { ...e, state: "owned" as const } : e,
    ),
  };
  return { ...page, blocks: { ...page.blocks, catalog: { ...catalog, data } } };
}

type Confirm = { entry: ShopEntryCard; key: string };

export function ShopView() {
  const t = useTranslations();
  const viewer = useViewer();
  const { back: Back } = useIcons();
  const query = useQuery({
    queryKey: shopQueryKey,
    queryFn: fetchShopPage,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;
  if (query.isError && !query.data) {
    const err = isApiError(query.error) ? query.error : null;
    const variant = gateOf(err?.status ?? null, err?.code ?? null);
    if (variant) return <ProfileGate variant={variant} />;
    return (
      <p className="pt-12 text-ink-muted" data-testid="shop-unavailable">
        {t("shop.unavailable")}
      </p>
    );
  }

  const catalog = blockData(query.data?.blocks.catalog);
  return (
    <div className="flex flex-col gap-4 pt-2 lg:-mx-[60px]">
      <HeaderLeading>
        <Button asChild variant="ghost" size="sm">
          <Link href="/profile" aria-label={t("shop.backLabel")}>
            <Back aria-hidden className="!size-[18px]" />
            {t("shop.back")}
          </Link>
        </Button>
      </HeaderLeading>
      {!query.data ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : !catalog ? (
        <p className="text-ink-muted" data-testid="shop-unavailable">
          {t("shop.unavailable")}
        </p>
      ) : (
        <ShopBody catalog={catalog} />
      )}
    </div>
  );
}

function ShopBody({ catalog }: { catalog: ShopCatalogCard }) {
  const t = useTranslations();
  const f = useFormatter();
  const client = useQueryClient();
  const slotLabel = useSlotTabLabel();
  const verses = useVerses();
  const { data: session } = useSessionInfo();
  const profile = useProfileBlocks();
  const equip = useEquip();
  const [tab, setTab] = useState(() => (catalog.slots[0] ? slotTab(catalog.slots[0]) : MINE));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [error, setError] = useState<PurchaseError | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const lock = useRef(false);

  const nickname = session?.subject?.display_name ?? t("nav.player");
  const equipped = new Set(
    (profile.inventory ?? []).filter((e) => e.equipped).map((e) => e.item.id),
  );
  const mineCount = profile.inventory?.length ?? null;

  const slot = tab.startsWith("slot:") ? tab.slice(5) : null;
  type Tile = { entry: ShopEntryCard; state: TileState };
  const tiles: Tile[] =
    slot !== null
      ? catalog.items
          .filter((e) => e.item.slot === slot)
          .map((entry) => ({
            entry,
            state:
              entry.state === "owned" && equipped.has(entry.item.id) ? "equipped" : entry.state,
          }))
      : (profile.inventory ?? []).map((e) => ({
          entry: { item: e.item, state: "owned" as const },
          state: e.equipped ? ("equipped" as const) : ("owned" as const),
        }));
  const current = tiles.find((x) => x.entry.item.id === selectedId) ?? tiles[0] ?? null;

  const expiresAt =
    current && profile.inventory
      ? (profile.inventory.find((e) => e.item.id === current.entry.item.id)?.expires_at ?? null)
      : null;

  const open = (entry: ShopEntryCard) => {
    // Пока идёт покупка, новый показ подтверждения не открываем.
    if (lock.current) return;
    setError(null);
    setStatus(null);
    // Один ключ на один показ подтверждения.
    setConfirm({ entry, key: newIdempotencyKey() });
  };

  const close = () => {
    setConfirm(null);
    setError(null);
  };

  const buy = async () => {
    if (!confirm || lock.current) return;
    lock.current = true;
    setBusy(true);
    setError(null);
    const { entry, key } = confirm;
    try {
      const result = await purchaseItem(entry.item.id, key);
      client.setQueryData<ShopPage>(shopQueryKey, (page) =>
        page ? markOwned(page, entry.item.id, result.balance_after) : page,
      );
      void client.invalidateQueries({ queryKey: profileQueryKey });
      setStatus(
        t("shop.bought", { title: entry.item.title, balance: verses(result.balance_after) }),
      );
      setConfirm(null);
    } catch (e) {
      const kind = purchaseErrorKey(e);
      if (kind === "alreadyOwned")
        client.setQueryData<ShopPage>(shopQueryKey, (page) =>
          page ? markOwned(page, entry.item.id, null) : page,
        );
      setRetryAfter(isApiError(e) ? e.retryAfterS : null);
      setError(kind);
      // Баланс на экране мог устареть (покупка с другой вкладки): перечитываем витрину.
      if (kind === "insufficientFunds") void client.invalidateQueries({ queryKey: shopQueryKey });
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  return (
    <>
      <header className="flex flex-col gap-2 px-0.5">
        <div className="flex items-center justify-between gap-3">
          <h1 className="font-display text-[28px] leading-none font-extrabold">
            {t("shop.title")}
          </h1>
          <p
            className="tabular inline-flex h-[38px] items-center rounded-full border-[1.5px] border-rule px-3.5 text-[15px] font-semibold text-music"
            data-testid="shop-balance"
            aria-live="polite"
          >
            {t("shop.balance", { balance: catalog.balance })}
          </p>
        </div>
        <p className="text-sm text-ink-muted">{t("shop.lead")}</p>
      </header>

      <div role="group" aria-label={t("shop.tabs")} className="flex flex-wrap gap-2">
        {catalog.slots.map((s) => (
          <Chip
            key={s}
            selected={tab === slotTab(s)}
            data-testid={`shop-tab-${s}`}
            onClick={() => {
              setTab(slotTab(s));
              setSelectedId(null);
            }}
          >
            {slotLabel(s)}
          </Chip>
        ))}
        <Chip
          selected={tab === MINE}
          className="ml-auto"
          data-testid="shop-tab-inventory"
          onClick={() => {
            setTab(MINE);
            setSelectedId(null);
          }}
        >
          {mineCount === null ? t("shop.tab.mine") : t("shop.tab.mineCount", { count: mineCount })}
        </Chip>
      </div>
      <div aria-live="polite" role="status" data-testid="shop-status" className="min-h-5 text-sm">
        {status}
      </div>

      {slot === null && profile.loading ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : slot === null && !profile.inventory ? (
        <p className="text-ink-muted" data-testid="inventory-unavailable">
          {t("shop.blockUnavailable")}
        </p>
      ) : tiles.length === 0 ? (
        <p
          className="text-ink-muted"
          data-testid={slot === null ? "inventory-empty" : "shop-empty"}
        >
          {slot === null ? t("shop.inventory.empty") : t("shop.empty")}
        </p>
      ) : (
        <div className="grid items-stretch gap-3.5 sm:grid-cols-[minmax(0,1fr)_240px]">
          <ul className="grid grid-cols-3 content-start gap-2.5 sm:grid-cols-5">
            {tiles.map(({ entry, state }) => (
              <li key={entry.item.id} className="contents">
                <ItemTile
                  item={entry.item}
                  state={state}
                  selected={current?.entry.item.id === entry.item.id}
                  onSelect={() => setSelectedId(entry.item.id)}
                  testId={
                    slot === null
                      ? `inventory-item-${entry.item.key}`
                      : `shop-item-${entry.item.key}`
                  }
                />
              </li>
            ))}
          </ul>
          {current && (
            <ItemPreview
              item={current.entry.item}
              nickname={nickname}
              appearance={profile.appearance}
            >
              {current.state === "available" && current.entry.item.price != null && (
                <Button
                  disabled={busy}
                  data-testid={`shop-buy-${current.entry.item.key}`}
                  onClick={() => open(current.entry)}
                >
                  {t("shop.buyFor", { price: verses(current.entry.item.price) })}
                </Button>
              )}
              {current.state === "locked" && (
                <p className="text-sm text-ink-muted" data-testid="shop-locked">
                  {current.entry.item.level_required != null
                    ? t("shop.unlockLevel", { level: current.entry.item.level_required })
                    : t("shop.state.locked")}
                </p>
              )}
              {(current.state === "owned" || current.state === "equipped") && (
                <Button
                  variant="secondary"
                  disabled={equip.pending !== null}
                  data-testid={`equip-item-${current.entry.item.key}`}
                  onClick={() =>
                    void equip.run(
                      current.entry.item.slot,
                      current.state === "equipped" ? null : current.entry.item.id,
                    )
                  }
                >
                  {current.state === "equipped"
                    ? t("shop.inventory.unequip")
                    : t("shop.inventory.equip")}
                </Button>
              )}
              {expiresAt && (
                <p className="text-xs text-ink-muted">
                  {t("shop.inventory.expires", {
                    date: f.dateTime(new Date(expiresAt), { dateStyle: "long" }),
                  })}
                </p>
              )}
              <div aria-live="polite" data-testid="equip-feedback">
                {equip.error && (
                  <p role="alert" className="text-sm text-miss">
                    {t(EQUIP_ERROR_KEYS[equip.error])}
                  </p>
                )}
              </div>
            </ItemPreview>
          )}
        </div>
      )}

      <PromoBlock />

      <Modal
        open={confirm !== null}
        onClose={busy ? () => undefined : close}
        aria-label={t("shop.confirm.label")}
        data-testid="shop-confirm-dialog"
        className="flex flex-col gap-3 p-5"
      >
        {confirm && (
          <>
            <h2 dir="auto" className="font-semibold">
              {t("shop.confirm.title", { title: confirm.entry.item.title })}
            </h2>
            <p className="text-ink-muted">
              {t("shop.confirm.text", {
                price: verses(confirm.entry.item.price ?? 0),
                balance: verses(Math.max(0, catalog.balance - (confirm.entry.item.price ?? 0))),
              })}
            </p>
            {error && (
              <p role="alert" className="text-miss" data-testid="shop-error">
                {error === "rateLimited" && retryAfter
                  ? t("shop.error.rateLimitedIn", { seconds: retryAfter })
                  : t(PURCHASE_ERROR_KEYS[error])}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => void buy()}
                disabled={busy}
                aria-busy={busy}
                data-testid="shop-confirm"
              >
                {busy ? t("shop.confirm.busy") : t("shop.confirm.yes")}
              </Button>
              <Button variant="secondary" onClick={close} disabled={busy}>
                {t("shop.confirm.no")}
              </Button>
            </div>
          </>
        )}
      </Modal>
    </>
  );
}
