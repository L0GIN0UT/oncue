"use client";
/**
 * Аудио-загадка комнаты. Отличия от соло (`features/game/clues/AudioClue`):
 *
 * - фрагмент играется **один раз** и останавливается на границе уровня (`meta.seconds`, но не
 *   дальше {@link MAX_CLIP_S}): файл клипа длиннее (30 с зациклены до 50 с под реверс), и без
 *   границы звучал бы «повтор» (пункт 57). Автоповтора нет; «Ещё раз» — по желанию игрока;
 * - граница держится и через `timeupdate`: в фоновой вкладке `requestAnimationFrame` замирает, а
 *   звук идёт дальше.
 *
 * Готовность фрагмента (для общего старта таймера) определяет экран: Blob уже скачан целиком.
 */
import { useEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { PlayButton } from "@/shared/ui/play-button";
import { cn } from "@/shared/lib/cn";
import type { ClueStageProps } from "@/features/game/clues/types";
import { useMuted } from "@/features/game/use-muted";

/** Длина звучащей части клипа, с: дальше в файле технический паддинг. */
export const MAX_CLIP_S = 30;

/** Граница воспроизведения уровня, с: длина подачи уровня, но не больше {@link MAX_CLIP_S}. */
export function playbackLimit(seconds: number | null | undefined, duration: number): number {
  const finite = Number.isFinite(duration) && duration > 0 ? duration : MAX_CLIP_S;
  const cap = Math.min(MAX_CLIP_S, finite);
  return seconds && seconds > 0 ? Math.min(seconds, cap) : cap;
}

export function MatchAudioClue({ clue, onProgress, scene = "stage" }: ClueStageProps) {
  const t = useTranslations();
  const format = useFormatter();
  const audio = useRef<HTMLAudioElement>(null);
  const frame = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [played, setPlayed] = useState(false);
  const muted = useMuted();
  const seconds = clue.meta.seconds ?? null;

  useEffect(() => {
    const el = audio.current;
    return () => {
      el?.pause();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [clue.objectUrl]);

  const stop = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    setPlaying(false);
    onProgress?.(null);
  };

  const tick = () => {
    const el = audio.current;
    if (!el) return;
    const limit = playbackLimit(seconds, el.duration);
    if (el.currentTime >= limit) {
      el.pause(); // конец уровня: один проход, без цикла
      stop();
      return;
    }
    onProgress?.(el.currentTime / limit);
    frame.current = requestAnimationFrame(tick);
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
    el.loop = false;
    try {
      await el.play();
      setPlaying(true);
      setPlayed(true);
      frame.current = requestAnimationFrame(tick);
    } catch {
      stop();
    }
  };

  const label = played
    ? t("match.replay")
    : seconds
      ? t("game.listen", { seconds: format.number(seconds, { maximumFractionDigits: 1 }) })
      : t("game.listenPlain");

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-4 py-2",
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
        loop={false}
        muted={muted}
        onTimeUpdate={() => {
          const el = audio.current;
          if (el && !el.paused && el.currentTime >= playbackLimit(seconds, el.duration)) {
            el.pause();
            stop();
          }
        }}
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
