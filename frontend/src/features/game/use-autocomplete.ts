/**
 * Подсказки ответа из catalog (`GET /api/catalog/v1/autocomplete`, D4): debounce, запрос
 * не длиннее `MAX_QUERY` символов, устаревший запрос отменяется (`AbortController`).
 */
import { useEffect, useState } from "react";
import { api, unwrap } from "@/shared/api/client";
import type { Domain, Suggestion } from "@/shared/api/types";

export const AUTOCOMPLETE_DEBOUNCE_MS = 200;
export const MAX_QUERY = 64;
export const AUTOCOMPLETE_LIMIT = 8;

export function normalizeQuery(raw: string): string {
  return raw.trim().slice(0, MAX_QUERY);
}

export function useAutocomplete(
  query: string,
  domain: Domain,
  debounceMs = AUTOCOMPLETE_DEBOUNCE_MS,
) {
  const [items, setItems] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const q = normalizeQuery(query);

  useEffect(() => {
    if (!q) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const data = unwrap(
          await api().catalog.GET("/v1/autocomplete", {
            params: { query: { q, domain, limit: AUTOCOMPLETE_LIMIT } },
            signal: controller.signal,
          }),
        );
        if (!controller.signal.aborted) setItems(data.items);
      } catch {
        if (!controller.signal.aborted) setItems([]);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, debounceMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [q, domain, debounceMs]);

  return { items: q ? items : [], loading: q ? loading : false };
}
