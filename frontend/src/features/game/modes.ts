/**
 * Каталог режимов game (`GET /v1/modes`, публичный, кэшируется): `kind=play` — режимы, которые
 * играются сами по себе; `kind=collection` — шаблоны режимов сборника (source `collection`) с
 * `round_options`/`min_items` конфига. Играбельность конкретного сборника — не здесь
 * (`GET /v1/collections/{id}/modes`).
 */
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import type { Domain, ModeOut } from "@/shared/api/types";

export type ModeKind = "play" | "collection";

/** Оба вида: режим сессии может быть и обычным, и шаблоном сборника. */
export const MODE_KINDS: readonly ModeKind[] = ["play", "collection"];

const STALE_MS = 5 * 60_000;

export const modeListKey = (domain: Domain | undefined, kind: ModeKind) =>
  ["modes", kind, domain ?? "any"] as const;

const modeListQuery = (domain: Domain | undefined, kind: ModeKind) => ({
  queryKey: modeListKey(domain, kind),
  queryFn: async (): Promise<ModeOut[]> =>
    unwrap(await api().game.GET("/v1/modes", { params: { query: { domain, kind } } })),
  staleTime: STALE_MS,
});

/** Режимы одного вида; без `domain` — всех доменов. */
export function useModeList(domain: Domain | undefined, kind: ModeKind) {
  return useQuery(modeListQuery(domain, kind));
}

/**
 * Режим сессии по ключу снимка: ищется во всех видах каталога домена, без разбора ключа —
 * сессия по сборнику получает уровни, подачу и название так же, как обычная.
 */
export function useSessionMode(domain: Domain | undefined, key: string | undefined) {
  const lists = useQueries({
    queries: MODE_KINDS.map((kind) => ({ ...modeListQuery(domain, kind), enabled: !!domain })),
  });
  const mode = key
    ? lists.flatMap((q) => q.data ?? []).find((m: ModeOut) => m.key === key)
    : undefined;
  return { mode, isPending: lists.some((q) => q.isPending) };
}
