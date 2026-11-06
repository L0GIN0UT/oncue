"use client";
/**
 * Обложка сборника — пресет из `GET /v1/meta` (градиент и эмодзи из конфига, постеров нет, D6).
 * Цвета берутся только в виде `#rrggbb`: в атрибут `style` не попадает произвольная строка.
 * Есть `artwork` (картинка первой единицы, ADR 0079) и `collectionId` — картинка рисуется поверх
 * пресета с теми же размерами; нет картинки, ссылка небезопасна или не открылась после
 * перевыдачи — остаётся пресет.
 */
import { useState } from "react";
import type { CoverArtwork, CoverPreset } from "@/shared/api/types";
import { safeImageUrl } from "@/features/cosmetics/CosmeticImage";
import { cn } from "@/shared/lib/cn";
import { useCollectionsMeta } from "./api";
import { useCollectionCoverArtwork } from "./cover-artwork";

const HEX = /^#[0-9a-fA-F]{6}$/;
/** Запасной градиент — токены темы (ADR 0049), а не литералы. */
const FALLBACK = ["var(--color-stage-raised)", "var(--color-rule)"];

export function coverGradient(preset: CoverPreset | undefined): string {
  const colors = (preset?.gradient ?? []).filter((c) => HEX.test(c));
  const stops =
    colors.length >= 2 ? colors : colors.length === 1 ? [colors[0]!, colors[0]!] : FALLBACK;
  return `linear-gradient(135deg, ${stops.join(", ")})`;
}

export function usePreset(id: string | null | undefined): CoverPreset | undefined {
  const meta = useCollectionsMeta();
  return id ? meta.data?.cover_presets?.find((p) => p.id === id) : undefined;
}

export function Cover({
  preset,
  className,
  size = "md",
  collectionId,
  artwork,
  alt = "",
}: {
  preset: string | null | undefined;
  className?: string;
  size?: "sm" | "md" | "lg";
  collectionId?: string;
  artwork?: CoverArtwork | null;
  /** Подпись картинки; по умолчанию она декоративная (название сборника рядом в тексте). */
  alt?: string;
}) {
  const p = usePreset(preset);
  const { url, onError } = useCollectionCoverArtwork(collectionId ?? "", artwork);
  const src = collectionId ? safeImageUrl(url, { allowMedia: true }) : null;
  const [failed, setFailed] = useState<string | null>(null);
  return (
    <div
      aria-hidden={alt ? undefined : true}
      className={cn(
        "relative grid shrink-0 place-items-center overflow-hidden rounded-[var(--radius-control)] ring-1 ring-rule",
        size === "sm" && "size-11 rounded-[10px] text-xl",
        size === "md" && "aspect-[4/3] w-full text-4xl",
        size === "lg" && "aspect-[4/3] w-full text-6xl sm:w-64",
        className,
      )}
      style={{ background: coverGradient(p) }}
    >
      <span>{p?.emoji ?? "🎞️"}</span>
      {src && failed !== src && (
        // Картинка приходит с media-pipeline, размеры заранее неизвестны: оптимизатор Next не нужен.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={alt}
          loading="lazy"
          decoding="async"
          data-testid="collection-cover-image"
          className="absolute inset-0 size-full object-cover"
          onError={() => {
            setFailed(src);
            onError();
          }}
        />
      )}
    </div>
  );
}
