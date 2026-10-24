"use client";
/**
 * Картинка косметики (ADR 0046): `static` и `animated` (animated WebP) рисуются `<img>` по
 * `asset_url` с публичного префикса `/cosmetics/`. При `prefers-reduced-motion: reduce` анимация
 * заменяется постером; постера нет — статичная заглушка (первый кадр из WebP недоступен).
 * `alt` — название предмета, загрузка ленивая.
 */
import { useSyncExternalStore } from "react";
import type { CosmeticCard } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  if (typeof matchMedia !== "function") return () => {};
  const mq = matchMedia(QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

const snapshot = () => typeof matchMedia === "function" && matchMedia(QUERY).matches;

/** Пользователь просит убрать движение. На сервере — `false`, на клиенте обновляется сразу. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}

/**
 * Допустимые адреса картинок косметики и UGC: только пути публичных префиксов gateway
 * (/cosmetics/, /ugc/, ADR 0046) без обратных слэшей, управляющих символов и «..».
 * Абсолютные адреса (https://…) не принимаются: публичного хоста в конфигурации приложения нет.
 */
const ALLOWED_PATH = /^\/(?:cosmetics|ugc)\/[^\\\u0000-\u001f\u007f]+$/;

export function safeImageUrl(
  url: string | null | undefined,
  options: { allowBlob?: boolean } = {},
): string | null {
  if (!url) return null;
  // Превью выбранного файла (URL.createObjectURL) — только там, где это явно разрешено.
  if (options.allowBlob && url.startsWith("blob:")) return url;
  if (!ALLOWED_PATH.test(url) || url.split("/").includes("..")) return null;
  return url;
}

type Props = {
  item: Pick<CosmeticCard, "title" | "media_kind" | "asset_url" | "poster_url">;
  className?: string;
  /** Украшение рядом с текстом (рамка, фон): пустой `alt`, заглушка скрыта от скринридеров. */
  decorative?: boolean;
  /** Разрешить blob:-адрес (только превью выбранного файла аватарки). */
  allowBlob?: boolean;
};

export function CosmeticImage({ item, className, decorative = false, allowBlob = false }: Props) {
  const reduced = useReducedMotion();
  const wanted =
    item.media_kind === "animated" && reduced ? (item.poster_url ?? null) : item.asset_url;
  const src = safeImageUrl(wanted, { allowBlob });
  if (!src) {
    return (
      <span
        {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": item.title })}
        data-placeholder="true"
        className={cn(
          "block rounded-[var(--radius-control)] bg-stage-raised ring-1 ring-rule",
          className,
        )}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element -- анимированный WebP: оптимизатор next/image его не сохранит
    <img
      src={src}
      alt={decorative ? "" : item.title}
      loading="lazy"
      decoding="async"
      className={cn("block object-cover", className)}
    />
  );
}
