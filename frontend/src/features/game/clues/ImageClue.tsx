"use client";
/**
 * Прогрессивный кадр и последовательность кадров: новый уровень проявляется поверх предыдущего
 * (плавно; при `prefers-reduced-motion` — сразу, см. globals.css). Подпись уровня строится из
 * `meta` выдачи — это `hint_fields` загадки ядра (`blur_px`, `crop`, `pixel_block`, `frame`); ассет
 * уже подготовлен media-pipeline, клиент ничего не размывает.
 */
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import type { ClueMeta } from "@/shared/api/types";
import type { ClueStageProps } from "./types";

/** Части подписи уровня картинки по `meta` (пусто — подсказки уровня нет). */
export function imageHintParts(
  meta: ClueMeta,
): (
  | { key: "game.frameCrop"; percent: number }
  | { key: "game.frameBlur"; px: number }
  | { key: "game.posterPixels"; px: number }
  | { key: "game.frameNumber"; frame: number }
)[] {
  const parts: ReturnType<typeof imageHintParts> = [];
  // frame_sequence: уровень — другой кадр фильма, а не более чёткий тот же.
  if (meta.frame != null && meta.frame > 0)
    parts.push({ key: "game.frameNumber", frame: meta.frame });
  if (meta.crop != null && meta.crop < 1)
    parts.push({ key: "game.frameCrop", percent: Math.round(meta.crop * 100) });
  if (meta.blur_px != null && meta.blur_px > 0)
    parts.push({ key: "game.frameBlur", px: Math.round(meta.blur_px) });
  if (meta.pixel_block != null && meta.pixel_block > 1)
    parts.push({ key: "game.posterPixels", px: meta.pixel_block });
  return parts;
}

export function ImageClue({ clue }: ClueStageProps) {
  const t = useTranslations();
  const parts = imageHintParts(clue.meta).map(({ key, ...values }) => t(key, values));
  const hint = parts.length > 0 ? parts.join(" · ") : null;
  const [previous, setPrevious] = useState<string | null>(null);
  const [current, setCurrent] = useState(clue.objectUrl);
  const [shown, setShown] = useState(false);

  if (clue.objectUrl !== current) {
    setPrevious(current);
    setCurrent(clue.objectUrl);
    setShown(false);
  }

  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, [current]);

  return (
    <figure className="flex w-full flex-col gap-2">
      <div className="relative aspect-video w-full overflow-hidden rounded-[var(--radius-control)] border-[1.5px] border-rule bg-night">
        {previous && (
          // eslint-disable-next-line @next/next/no-img-element -- blob: из ответа game, оптимизация Next неприменима
          <img
            src={previous}
            alt=""
            aria-hidden
            className="absolute inset-0 size-full object-cover"
          />
        )}
        {/* eslint-disable-next-line @next/next/no-img-element -- blob: из ответа game */}
        <img
          src={current}
          alt={t("game.frameAlt", { level: clue.level })}
          data-testid="clue-image"
          className="absolute inset-0 size-full object-cover transition-opacity duration-700 ease-[var(--ease-reveal)] motion-reduce:transition-none"
          style={{ opacity: shown ? 1 : 0 }}
        />
      </div>
      {hint && (
        <figcaption data-testid="clue-image-hint" className="text-center text-sm text-ink-muted">
          {hint}
        </figcaption>
      )}
    </figure>
  );
}
