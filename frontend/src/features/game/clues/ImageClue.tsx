"use client";
/**
 * Прогрессивный кадр: новый уровень проявляется поверх предыдущего (плавно; при
 * `prefers-reduced-motion` — сразу, см. globals.css).
 */
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import type { ClueStageProps } from "./types";

export function ImageClue({ clue }: ClueStageProps) {
  const t = useTranslations();
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
    <div className="relative aspect-video w-full overflow-hidden rounded-[var(--radius-control)] bg-night ring-1 ring-rule">
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
        className="absolute inset-0 size-full object-cover transition-opacity duration-700 ease-[var(--ease-reveal)]"
        style={{ opacity: shown ? 1 : 0 }}
      />
    </div>
  );
}
