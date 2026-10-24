"use client";
/**
 * Магазин `/shop` (этап 5b): вкладки по слотам (порядок и состав слотов — из данных bff, код
 * ключей не знает), карточки предметов, покупка с подтверждением, вкладки «Инвентарь» и
 * «Оформление». Покупка — `POST /v1/shop/items/{id}/purchase` прямо в progression: один
 * `Idempotency-Key` на один показ подтверждения (повтор после сбоя — тот же ключ, новый показ —
 * новый), цена и условия проверяются сервером. Баланс и состояние карточки обновляются из ответа.
 */
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { blockData } from "@/shared/api/blocks";
import { isApiError } from "@/shared/api/errors";
import type { ShopCatalogCard, ShopEntryCard, ShopPage } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useViewer } from "@/features/auth/session";
import { profileQueryKey } from "@/features/progression/api";
import { gateOf, ProfileGate } from "@/features/progression/ProfileView";
import { fetchShopPage, newIdempotencyKey, purchaseItem, shopQueryKey } from "./api";
import { purchaseErrorKey, type PurchaseError } from "./errors";
import { AppearancePanel, InventoryPanel } from "./InventoryPanels";
import { ItemCard } from "./ItemCard";
import { useSlotTabLabel, useVerses } from "./labels";
import { Tabs, type TabItem } from "./Tabs";

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

const PANEL_ID = "shop-panel";
const INVENTORY = "inventory";
const APPEARANCE = "appearance";
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

/** Фокус по Tab ходит по кругу внутри модального диалога. */
function trapFocus(e: KeyboardEvent<HTMLElement>, root: HTMLElement | null) {
  const items = root?.querySelectorAll<HTMLElement>("button:not(:disabled), [href], input, select");
  if (!items?.length) return;
  const first = items[0]!;
  const last = items[items.length - 1]!;
  const active = document.activeElement;
  if (e.shiftKey && active === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && active === last) {
    e.preventDefault();
    first.focus();
  }
}

type Confirm = { entry: ShopEntryCard; key: string };

export function ShopView() {
  const t = useTranslations();
  const viewer = useViewer();
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
    <div className="flex flex-col gap-6 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-3xl font-bold">{t("shop.title")}</h1>
        <p className="max-w-[60ch] text-ink-muted">{t("shop.lead")}</p>
      </header>
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
  const client = useQueryClient();
  const slotLabel = useSlotTabLabel();
  const verses = useVerses();
  const [active, setActive] = useState(() =>
    catalog.slots[0] ? slotTab(catalog.slots[0]) : INVENTORY,
  );
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [error, setError] = useState<PurchaseError | null>(null);
  const [retryAfter, setRetryAfter] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const lock = useRef(false);
  const opener = useRef<HTMLElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (confirm) confirmRef.current?.focus();
  }, [confirm]);

  const tabs: TabItem[] = [
    ...catalog.slots.map((slot) => ({
      id: slotTab(slot),
      label: slotLabel(slot),
      testId: `shop-tab-${slot}`,
    })),
    { id: INVENTORY, label: t("shop.tab.inventory"), testId: "shop-tab-inventory" },
    { id: APPEARANCE, label: t("shop.tab.appearance"), testId: "shop-tab-appearance" },
  ];

  const open = (entry: ShopEntryCard, button: HTMLElement) => {
    // Пока идёт покупка, новый показ подтверждения не открываем.
    if (lock.current) return;
    opener.current = button;
    setError(null);
    setStatus(null);
    // Один ключ на один показ подтверждения.
    setConfirm({ entry, key: newIdempotencyKey() });
  };

  const close = () => {
    setConfirm(null);
    setError(null);
    opener.current?.focus();
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

  const slot = active.startsWith("slot:") ? active.slice(5) : null;
  const items = slot ? catalog.items.filter((e) => e.item.slot === slot) : [];

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          tabs={tabs}
          active={active}
          onChange={setActive}
          label={t("shop.tabs")}
          panelId={PANEL_ID}
        />
        <p className="tabular font-medium" data-testid="shop-balance" aria-live="polite">
          {t("shop.balance", { balance: catalog.balance })}
        </p>
      </div>
      <div aria-live="polite" role="status" data-testid="shop-status" className="min-h-6 text-sm">
        {status}
      </div>

      {confirm && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="shop-confirm-title"
          data-testid="shop-confirm-dialog"
          onKeyDown={(e) => {
            if (e.key === "Escape" && !busy) close();
            if (e.key === "Tab") trapFocus(e, dialogRef.current);
          }}
          className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage-raised p-5 ring-2 ring-[var(--accent,var(--color-music))]"
        >
          <h2 id="shop-confirm-title" dir="auto" className="font-display text-lg font-semibold">
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
              ref={confirmRef}
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
        </div>
      )}

      <div id={PANEL_ID} role="tabpanel" tabIndex={-1} className="outline-none">
        {slot !== null ? (
          items.length ? (
            <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
              {items.map(({ item, state }) => (
                <ItemCard
                  key={item.id}
                  item={item}
                  state={state}
                  actions={
                    state === "available" && item.price != null ? (
                      <Button
                        size="sm"
                        disabled={busy}
                        data-testid={`shop-buy-${item.key}`}
                        onClick={(e) => open({ item, state }, e.currentTarget)}
                      >
                        {t("shop.buy")}
                      </Button>
                    ) : null
                  }
                />
              ))}
            </ul>
          ) : (
            <p className="text-ink-muted">{t("shop.empty")}</p>
          )
        ) : active === INVENTORY ? (
          <InventoryPanel />
        ) : (
          <AppearancePanel slots={catalog.slots} />
        )}
      </div>
    </>
  );
}
