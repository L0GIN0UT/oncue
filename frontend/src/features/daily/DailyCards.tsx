"use client";
/**
 * Запрос страницы «главная» bff (`GET /api/web/v1/pages/home` с cookie, D2): SSR отдаёт публичный
 * каркас, клиент догружает личный статус. Здесь же восстановление сессии для личных блоков.
 */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, unwrap } from "@/shared/api/client";
import type { HomePage } from "@/shared/api/types";
import { recoverSession } from "@/features/auth/session";

export const homeQueryKey = ["page", "home"] as const;

export function useHomePage(initial: HomePage | null, { enabled = true } = {}) {
  return useQuery({
    enabled,
    queryKey: homeQueryKey,
    queryFn: async (): Promise<HomePage> => unwrap(await api().web.GET("/v1/pages/home")),
    initialData: initial ?? undefined,
  });
}

/**
 * Личный блок дейликов bff отдаёт только субъекту; анониму или с просроченным access —
 * `unavailable` (game отвечает 401, а bff прячет его в блоке, и общий refresh на 401 не
 * срабатывает). Поэтому фронтенд сам восстанавливает сессию — refresh при живой cookie
 * `gv_csrf`, иначе гостевая — и перезапрашивает блок. Только один раз: недоступность из-за
 * сбоя апстрима так не лечится, и цикла запросов быть не должно.
 */
export function useRecoverPersonalBlocks(
  statuses: (string | undefined)[],
  refresh: () => unknown,
): boolean {
  const recovered = useRef(false);
  const [settled, setSettled] = useState(false);
  const mounted = useRef(true);
  const unavailable = statuses.includes("unavailable");
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (recovered.current || !unavailable) return;
    recovered.current = true;
    void recoverSession()
      .then(refresh)
      .catch(() => undefined)
      .finally(() => {
        // Экран мог закрыться, пока шло восстановление: состояние уже не нужно.
        if (mounted.current) setSettled(true);
      });
  }, [unavailable, refresh]);
  // Восстановление и повторный запрос завершены: дальнейшая недоступность — не ожидание.
  return settled;
}
