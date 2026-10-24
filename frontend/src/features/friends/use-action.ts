"use client";
/**
 * Выполнение мутации social с ключом идемпотентности и понятной ошибкой.
 *
 * Ключ создаётся на «намерение» (область `scope`) и сохраняется после сетевого сбоя: повторное
 * нажатие уходит с тем же ключом, сервер вернёт тот же результат. Любой ответ сервера
 * (успех или отказ) завершает намерение — следующее нажатие получит новый ключ.
 */
import { useCallback, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { NetworkError } from "@/shared/api/errors";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { newIdempotencyKey } from "@/shared/lib/id";
import { socialErrorKey } from "./errors";

export function useSocialAction() {
  const t = useTranslations();
  const dt = useDynamicT();
  const keys = useRef(new Map<string, string>());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = useCallback(
    async <T>(
      scope: string,
      fn: (key: string) => Promise<T>,
    ): Promise<{ ok: true; value: T } | { ok: false }> => {
      const key = keys.current.get(scope) ?? newIdempotencyKey();
      keys.current.set(scope, key);
      setBusy(true);
      setError(null);
      try {
        const value = await fn(key);
        keys.current.delete(scope);
        return { ok: true, value };
      } catch (e) {
        if (!(e instanceof NetworkError)) keys.current.delete(scope);
        setError(dt(socialErrorKey(e), t("social.error.generic")));
        return { ok: false };
      } finally {
        setBusy(false);
      }
    },
    [dt, t],
  );

  return { run, error, busy, clearError: () => setError(null) };
}
