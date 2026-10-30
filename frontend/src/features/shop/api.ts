/**
 * Магазин, оформление, промокод и своя аватарка (этап 5b). Чтение витрины — bff (`/v1/pages/shop`),
 * мутации — прямо в progression через gateway (ADR 0017; bff мутаций не имеет). Цена и условия —
 * только на сервере: клиент шлёт лишь id предмета и ключ идемпотентности.
 */
import { api, unwrap } from "@/shared/api/client";
import { ApiError } from "@/shared/api/errors";
import type { PromoRedeemResult, PurchaseResult, ShopPage } from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

export const shopQueryKey = ["page", "shop"] as const;

export async function fetchShopPage(): Promise<ShopPage> {
  return unwrap(await api().web.GET("/v1/pages/shop"));
}

const idem = (key: string) => ({ header: { "Idempotency-Key": key } });

/** Ключ идемпотентности на один показ подтверждения (повтор после сбоя сети — с тем же). */
export { newIdempotencyKey };

export async function purchaseItem(itemId: string, key: string): Promise<PurchaseResult> {
  return unwrap(
    await api().progression.POST("/v1/shop/items/{item_id}/purchase", {
      params: { path: { item_id: itemId }, ...idem(key) },
    }),
  );
}

/** Надеть предмет в слот (`itemId = null` — снять). Идемпотентно по природе операции. */
export async function equipItem(slot: string, itemId: string | null) {
  return unwrap(
    await api().progression.PUT("/v1/me/appearance/{slot}", {
      params: { path: { slot } },
      body: { item_id: itemId },
    }),
  );
}

export async function redeemPromo(code: string, key: string): Promise<PromoRedeemResult> {
  return unwrap(
    await api().progression.POST("/v1/promo/redeem", {
      params: idem(key),
      body: { code },
    }),
  );
}

export async function submitAvatar(file: File, key: string) {
  return unwrap(
    await api().progression.POST("/v1/me/avatar", {
      params: idem(key),
      // multipart: поле `file`; Content-Type с границей выставит браузер.
      body: { file: "" },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file);
        return form;
      },
    }),
  );
}

export function statusOf(e: unknown): number | null {
  return e instanceof ApiError ? e.status : null;
}
