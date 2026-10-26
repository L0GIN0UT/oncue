"use client";
/**
 * Аудио-загадка: фрагмент уровня уже скачан в `Blob`. Воспроизведение — только по жесту
 * (браузеры блокируют автозапуск), повтор не запрашивает новую ссылку. Сцена `blackout` —
 * тот же плеер на чёрном экране (звук фильма, кадр — в ревиле).
 */
import { useEffect, useRef, useState } from "react";
import { useTranslations, useFormatter } from "next-intl";
import { PlayButton } from "@/shared/ui/play-button";
import { cn } from "@/shared/lib/cn";
import type { ClueStageProps } from "./types";

export function AudioClue({ clue, onProgress, scene = "stage" }: ClueStageProps) {
  const t = useTranslations();
  const format = useFormatter();
  const audio = useRef<HTMLAudioElement>(null);
  const frame = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const seconds = clue.meta.seconds ?? null;

  useEffect(() => {
    const el = audio.current;
    return () => {
      el?.pause();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [clue.objectUrl]);

  const tick = () => {
    const el = audio.current;
    if (!el) return;
    const duration = seconds ?? (Number.isFinite(el.duration) ? el.duration : 0);
    onProgress?.(duration > 0 ? el.currentTime / duration : null);
    frame.current = requestAnimationFrame(tick);
  };

  const stop = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    setPlaying(false);
    onProgress?.(null);
  };

  const toggle = async () => {
    const el = audio.current;
    if (!el) return;
    if (playing) {
      el.pause();
      stop();
      return;
    }
    el.currentTime = 0;
    try {
      await el.play();
      setPlaying(true);
      frame.current = requestAnimationFrame(tick);
    } catch {
      stop();
    }
  };

  const label = seconds
    ? t("game.listen", { seconds: format.number(seconds, { maximumFractionDigits: 1 }) })
    : t("game.listenPlain");

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
      <audio
        ref={audio}
        src={clue.objectUrl}
        preload="auto"
        onEnded={stop}
        data-testid="clue-audio"
      />
      <PlayButton
        playing={playing}
        onClick={toggle}
        aria-label={playing ? t("game.pause") : label}
      />
      {scene === "blackout" && <p className="text-sm text-white/70">{t("game.blackoutHint")}</p>}
    </div>
  );
}
