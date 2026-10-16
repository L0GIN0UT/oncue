/**
 * Медиатека (этап 3b, D11, D12). Экраны полок — через bff (`/v1/pages/library…`), сохранение
 * и свои полки — прямо в library (`/api/library/v1`, cookie + CSRF + `Idempotency-Key`).
 */
import { api, unwrap } from "@/shared/api/client";
import type {
  ItemShelves,
  LibraryPage,
  LibraryShelfPage,
  SaveResult,
  SaveSource,
  ShelfOut,
} from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

export const libraryKeys = {
  shelves: ["library", "shelves"] as const,
  item: (id: string) => ["library", "item", id] as const,
  page: ["library", "page"] as const,
  shelf: (ref: string) => ["library", "shelf", ref] as const,
};

const key = () => ({ "Idempotency-Key": newIdempotencyKey() });

export async function fetchShelves(): Promise<ShelfOut[]> {
  return unwrap(await api().library.GET("/v1/shelves"));
}

export async function fetchItemShelves(mediaItemId: string): Promise<ItemShelves> {
  return unwrap(
    await api().library.GET("/v1/items/{media_item_id}/shelves", {
      params: { path: { media_item_id: mediaItemId } },
    }),
  );
}

/** Положить единицу на полку или убрать с неё (идемпотентно, D12). */
export async function toggleShelfItem(
  ref: string,
  mediaItemId: string,
  on: boolean,
  source: SaveSource,
): Promise<SaveResult> {
  const path = { shelf_ref: ref, media_item_id: mediaItemId };
  const l = api().library;
  return unwrap(
    on
      ? await l.PUT("/v1/shelves/{shelf_ref}/items/{media_item_id}", {
          params: { header: key(), path },
          body: { source },
        })
      : await l.DELETE("/v1/shelves/{shelf_ref}/items/{media_item_id}", {
          params: { header: key(), path, query: { source } },
        }),
  );
}

export async function createShelf(title: string): Promise<ShelfOut> {
  return unwrap(
    await api().library.POST("/v1/shelves", { params: { header: key() }, body: { title } }),
  );
}

export async function renameShelf(shelfId: string, title: string): Promise<ShelfOut> {
  return unwrap(
    await api().library.PATCH("/v1/shelves/{shelf_id}", {
      params: { header: key(), path: { shelf_id: shelfId } },
      body: { title },
    }),
  );
}

export async function deleteShelf(shelfId: string): Promise<void> {
  unwrap(
    await api().library.DELETE("/v1/shelves/{shelf_id}", {
      params: { header: key(), path: { shelf_id: shelfId } },
    }),
  );
}

export async function fetchLibraryPage(): Promise<LibraryPage> {
  return unwrap(await api().web.GET("/v1/pages/library"));
}

export async function fetchShelfPage(
  ref: string,
  cursor: string | null,
): Promise<LibraryShelfPage> {
  return unwrap(
    await api().web.GET("/v1/pages/library/{shelf_ref}", {
      params: { path: { shelf_ref: ref }, query: { cursor: cursor ?? undefined } },
    }),
  );
}

/** ref исключающих полок: в конфиге — ключи системных полок (`watched` → `system:watched`). */
export function exclusiveRefs(shelf: Pick<ShelfOut, "exclusive_with">): string[] {
  return (shelf.exclusive_with ?? []).map((k) => (k.includes(":") ? k : `system:${k}`));
}

/**
 * Оптимистичное состояние полок единицы после переключения `ref`: сохранение на полку с
 * `exclusive_with` сразу убирает единицу с исключающих полок (сервер ответит `removed_from`).
 */
export function optimisticShelves(current: string[], shelf: ShelfOut, on: boolean): string[] {
  if (!on) return current.filter((r) => r !== shelf.ref);
  const drop = new Set(exclusiveRefs(shelf));
  return [...current.filter((r) => !drop.has(r) && r !== shelf.ref), shelf.ref];
}
