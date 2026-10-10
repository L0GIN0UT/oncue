"use client";
/**
 * Аудио-загадка: фрагмент уровня уже скачан в `Blob`. Воспроизведение — только по жесту
 * (браузеры блокируют автозапуск), повтор не запрашивает новую ссылку.
 */
import { useEffect, useRef, useState } from "react";
import { useTranslations, useFormatter } from "next-intl";
import { Pause, Play } from "lucide-react";
import type { ClueStageProps } from "./types";

export function AudioClue({ clue, onProgress }: ClueStageProps) {
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
    <div className="flex flex-col items-center gap-4 py-6">
      <audio
        ref={audio}
        src={clue.objectUrl}
        preload="auto"
        onEnded={stop}
        data-testid="clue-audio"
      />
      <button
        type="button"
        onClick={toggle}
        aria-pressed={playing}
        className="group grid size-28 place-items-center rounded-full bg-[var(--accent)] text-night shadow-[0_10px_0_-3px_var(--accent-deep)] transition-transform active:translate-y-1 sm:size-32"
      >
        {playing ? (
          <Pause className="size-10" aria-hidden />
        ) : (
          <Play className="size-10 translate-x-0.5" aria-hidden />
        )}
        <span className="sr-only">{playing ? t("game.pause") : label}</span>
      </button>
      <p className="tabular font-display text-lg font-semibold" aria-hidden>
        {label}
      </p>
    </div>
  );
}
