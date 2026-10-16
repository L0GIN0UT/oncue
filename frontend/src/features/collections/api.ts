/**
 * Запросы сборников (этап 3b). Чтение экранов — через bff (`/api/web/v1/pages/collections…`),
 * список с курсором и мутации — прямо в collections через gateway (`/api/collections/v1`, cookie +
 * CSRF + `Idempotency-Key`, как ходы game). Ответы — только данные контракта.
 */
import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import type {
  CatalogQuery,
  CollectionCatalogPage,
  CollectionDetail,
  CollectionPageOut,
  CollectionsMeta,
  CollectionsPage,
  DraftBody,
  DraftOut,
  FavoriteOut,
  LikeOut,
  MyCollectionsPage,
} from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

export const collectionKeys = {
  meta: ["collections", "meta"] as const,
  showcase: (filters: CatalogQuery) => ["collections", "showcase", filters] as const,
  list: (filters: CatalogQuery) => ["collections", "list", filters] as const,
  page: (id: string) => ["collections", "page", id] as const,
  detail: (id: string) => ["collections", "detail", id] as const,
  draft: (id: string) => ["collections", "draft", id] as const,
  mine: ["collections", "mine"] as const,
};

/** Словари конструктора: лимиты, пресеты обложек, теги (`GET /v1/meta`, меняются редко). */
export function useCollectionsMeta() {
  return useQuery({
    queryKey: collectionKeys.meta,
    queryFn: async (): Promise<CollectionsMeta> => unwrap(await api().collections.GET("/v1/meta")),
    staleTime: 10 * 60_000,
  });
}

/** Витрины каталога без поиска (bff: `featured`, `popular`, `new`). */
export async function fetchShowcase(filters: CatalogQuery): Promise<CollectionsPage> {
  return unwrap(
    await api().web.GET("/v1/pages/collections", {
      params: { query: { ...filters, q: undefined, cursor: undefined } },
    }),
  );
}

export const PAGE_LIMIT = 20;

/** Страница каталога по курсору (поиск, фильтры, сортировка). */
export async function fetchCatalogPage(
  filters: CatalogQuery,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<CollectionCatalogPage> {
  return unwrap(
    await api().collections.GET("/v1/collections", {
      params: { query: { ...filters, limit: PAGE_LIMIT, cursor: cursor ?? undefined } },
      signal,
    }),
  );
}

/** Страница сборника (bff): карточка, режимы game и отношение пользователя `me`. */
export async function fetchCollectionPage(id: string): Promise<CollectionPageOut> {
  return unwrap(
    await api().web.GET("/v1/pages/collections/{collection_id}", {
      params: { path: { collection_id: id } },
    }),
  );
}

/** Сборник из collections (название для шэра итога). */
export async function fetchCollection(id: string): Promise<CollectionDetail> {
  return unwrap(
    await api().collections.GET("/v1/collections/{collection_id}", {
      params: { path: { collection_id: id } },
    }),
  );
}

const key = (k?: string) => ({ header: { "Idempotency-Key": k ?? newIdempotencyKey() } });

export async function createCollection(
  domain: DraftOut["domain"],
  title: string,
): Promise<DraftOut> {
  return unwrap(
    await api().collections.POST("/v1/collections", {
      params: key(),
      body: { domain, title },
    }),
  );
}

export async function fetchDraft(id: string): Promise<DraftOut> {
  return unwrap(
    await api().collections.GET("/v1/collections/{collection_id}/draft", {
      params: { path: { collection_id: id } },
    }),
  );
}

/** Сохранить черновик поверх `baseRevision` (409 `collections.draft_conflict` — устарела). */
export async function saveDraft(
  id: string,
  baseRevision: number,
  body: DraftBody,
  clientLabel: string,
  idempotencyKey?: string,
): Promise<DraftOut> {
  return unwrap(
    await api().collections.PUT("/v1/collections/{collection_id}/draft", {
      params: { ...key(idempotencyKey), path: { collection_id: id } },
      body: { base_revision: baseRevision, body, client_label: clientLabel },
    }),
  );
}

export async function publishDraft(id: string, revision: number): Promise<CollectionDetail> {
  return unwrap(
    await api().collections.POST("/v1/collections/{collection_id}/publish", {
      params: { ...key(), path: { collection_id: id } },
      body: { draft_revision: revision },
    }),
  );
}

export async function deleteCollection(id: string): Promise<void> {
  unwrap(
    await api().collections.DELETE("/v1/collections/{collection_id}", {
      params: { ...key(), path: { collection_id: id } },
    }),
  );
}

export async function setLike(id: string, on: boolean): Promise<LikeOut> {
  const init = { params: { ...key(), path: { collection_id: id } } };
  const c = api().collections;
  return unwrap(
    on
      ? await c.PUT("/v1/collections/{collection_id}/like", init)
      : await c.DELETE("/v1/collections/{collection_id}/like", init),
  );
}

export async function setFavorite(id: string, on: boolean): Promise<FavoriteOut> {
  const init = { params: { ...key(), path: { collection_id: id } } };
  const c = api().collections;
  return unwrap(
    on
      ? await c.PUT("/v1/collections/{collection_id}/favorite", init)
      : await c.DELETE("/v1/collections/{collection_id}/favorite", init),
  );
}

export async function fetchMyCollections(cursor: string | null): Promise<MyCollectionsPage> {
  return unwrap(
    await api().collections.GET("/v1/me/collections", {
      params: { query: { cursor: cursor ?? undefined } },
    }),
  );
}

/** Какие единицы играбельны в режиме сборника (game, только пользователям; без названий). */
export async function fetchPlayability(modeKey: string, ids: string[]): Promise<string[]> {
  const out = unwrap(
    await api().game.POST("/v1/modes/{mode_key}/playability", {
      params: { path: { mode_key: modeKey } },
      body: { media_item_ids: ids },
    }),
  );
  return out.playable_ids;
}
