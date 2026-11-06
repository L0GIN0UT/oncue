"use client";
/**
 * Картинка косметики (ADR 0046): `static` и `animated` (animated WebP) рисуются `<img>` по
 * `asset_url` с публичного префикса `/cosmetics/`. При `prefers-reduced-motion: reduce` анимация
 * заменяется постером; постера нет — статичная заглушка (первый кадр из WebP недоступен).
 * `alt` — название предмета, загрузка ленивая. Нет картинки (нет `asset_url`, небезопасный адрес,
 * animated без постера при reduced-motion) — заглушка с иконкой слота, а не пустой квадрат.
 */
import { useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import {
  Image as ImageIcon,
  ImageUp,
  Sparkles,
  SquareDashed,
  User,
  type LucideIcon,
} from "lucide-react";
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

/**
 * Иконка заглушки по слоту. Слоты — данные (конфиг progression), поэтому неизвестный слот не
 * ломает экран: у него общая иконка. «Своя аватарка» (право на загрузку) — отдельный вид.
 */
const SLOT_ICONS: Record<string, LucideIcon> = {
  avatar: User,
  frame: SquareDashed,
  background: ImageIcon,
};

type Props = {
  item: Pick<CosmeticCard, "title" | "media_kind" | "asset_url" | "poster_url"> & {
    /** Слот предмета: от него зависит иконка заглушки. */
    slot?: string;
    /**
     * Товар «Своя аватарка» (ADR 0048): по смыслу у него нет картинки, это право загрузить свою.
     * Заглушка получает иконку загрузки, чтобы не выглядеть сломанной картинкой.
     */
    grants_custom_avatar?: boolean;
  };
  className?: string;
  /** Украшение рядом с текстом (рамка, фон): пустой `alt`, заглушка скрыта от скринридеров. */
  decorative?: boolean;
  /** Разрешить blob:-адрес (только превью выбранного файла аватарки). */
  allowBlob?: boolean;
  /** Подпись «Загрузите свою» под иконкой заглушки «Своей аватарки» (крупные плитки и предпросмотр). */
  showLabel?: boolean;
};

function CustomUploadLabel() {
  const t = useTranslations();
  return (
    <span className="px-1 text-center text-[10px] leading-tight text-ink-muted">
      {t("shop.customAvatar.label")}
    </span>
  );
}

export function CosmeticImage({
  item,
  className,
  decorative = false,
  allowBlob = false,
  showLabel = false,
}: Props) {
  const reduced = useReducedMotion();
  const wanted =
    item.media_kind === "animated" && reduced ? (item.poster_url ?? null) : item.asset_url;
  const src = safeImageUrl(wanted, { allowBlob });
  if (!src) {
    const customUpload = item.grants_custom_avatar === true;
    const kind = customUpload
      ? "custom_avatar"
      : item.slot && item.slot in SLOT_ICONS
        ? item.slot
        : "other";
    const Icon = customUpload ? ImageUp : (SLOT_ICONS[item.slot ?? ""] ?? Sparkles);
    return (
      <span
        {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": item.title })}
        data-placeholder="true"
        data-placeholder-kind={kind}
        className={cn(
          "flex flex-col items-center justify-center gap-0.5 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage-raised text-ink-dim",
          className,
        )}
      >
        <Icon aria-hidden className="size-1/2 max-h-8 max-w-8" />
        {customUpload && showLabel && <CustomUploadLabel />}
      </span>
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
