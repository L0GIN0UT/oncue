"use client";
/**
 * Спрятанный элемент сезонного события (этап 5d): рисуется, только если у активного события
 * есть открытая находка `ui_spot` для этой страницы (данные — страница события bff, запрос
 * общий с экраном события). Клик засчитывает находку прямо в progression (CSRF и
 * `Idempotency-Key` ставит общий клиент), затем обновляет баннер и страницу события.
 */
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import { newIdempotencyKey } from "@/shared/lib/id";
import { useIcons } from "@/shared/ui/theme-provider";
import { useToast } from "@/shared/ui/toast";
import { homeQueryKey } from "@/features/daily/DailyCards";
import { shellQueryKey } from "@/features/notifications/api";
import { profileQueryKey } from "@/features/progression/api";
import { claimFind, eventPageQueryKey } from "./api";
import { openSpotFor, type SpotPageKey } from "./logic";
import { useEventPage, useShellEvent } from "./use-event";

export function EventSpot({ pageKey }: { pageKey: SpotPageKey }) {
  const t = useTranslations();
  const icons = useIcons();
  const toast = useToast();
  const client = useQueryClient();
  const banner = useShellEvent();
  const page = useEventPage(banner?.key);
  const find = openSpotFor(page.data, pageKey);
  const [pending, setPending] = useState(false);
  // Один ключ на находку: повтор после сбоя сети не выдаст награду дважды.
  const keys = useRef(new Map<string, string>());
  const lock = useRef(false);

  if (!banner || !find) return null;
  const Icon = icons.spot;

  const refresh = () =>
    Promise.allSettled([
      client.invalidateQueries({ queryKey: eventPageQueryKey(banner.key) }),
      client.invalidateQueries({ queryKey: shellQueryKey }),
      client.invalidateQueries({ queryKey: homeQueryKey }),
      client.invalidateQueries({ queryKey: profileQueryKey }),
    ]);

  const claim = async () => {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    try {
      let key = keys.current.get(find.key);
      if (!key) {
        key = newIdempotencyKey();
        keys.current.set(find.key, key);
      }
      await claimFind(banner.key, find.key, key);
      toast(t("events.spot.claimed"));
    } catch (e) {
      // Событие кончилось или находка уже не открыта — данные устарели, обновим их.
      toast(
        isApiError(e, "progression.event_claim_rate_limited")
          ? t("events.spot.tooOften")
          : t("events.spot.failed"),
      );
    } finally {
      lock.current = false;
      setPending(false);
    }
    await refresh();
  };

  return (
    <button
      type="button"
      onClick={() => void claim()}
      disabled={pending}
      aria-label={t("events.spot.label")}
      data-testid="event-spot"
      data-find={find.key}
      className="grid size-11 place-items-center self-end rounded-full text-ink-faint opacity-60 transition-opacity hover:text-[var(--color-film)] hover:opacity-100 focus-visible:opacity-100"
    >
      <Icon aria-hidden className="size-5" />
    </button>
  );
}
