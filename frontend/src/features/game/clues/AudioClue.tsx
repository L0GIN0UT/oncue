"use client";
/**
 * Аудио-загадка: фрагмент уровня уже скачан в `Blob`. Воспроизведение — только по жесту
 * (браузеры блокируют автозапуск), повтор и перемотка не запрашивают новую ссылку; перемотка —
 * только внутри открытого фрагмента уровня (`SeekableAudio`). Сцена `blackout` — тот же плеер
 * на чёрном экране (звук фильма, кадр — в ревиле).
 */
import { useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { SeekableAudio } from "./SeekableAudio";
import type { ClueStageProps } from "./types";

export function AudioClue({ clue, onProgress, scene = "stage" }: ClueStageProps) {
  const t = useTranslations();
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-4 py-2",
        // Чёрная сцена: звук фильма без картинки — экран «погашен», кадр будет в ответе раунда.
        scene === "blackout" &&
          "aspect-video w-full justify-center rounded-[var(--radius-control)] border-[1.5px] border-rule bg-black text-white",
      )}
      data-scene={scene}
      data-testid="clue-scene"
    >
      <SeekableAudio
        key={clue.objectUrl}
        objectUrl={clue.objectUrl}
        seconds={clue.meta.seconds ?? null}
        onProgress={onProgress}
      />
      {scene === "blackout" && <p className="text-sm text-white/70">{t("game.blackoutHint")}</p>}
    </div>
  );
}
