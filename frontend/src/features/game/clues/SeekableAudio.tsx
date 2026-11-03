"use client";
/**
 * Плеер фрагмента: кнопка Play и полоса перемотки (`SeekBar`). Играет один проход до границы
 * (`playbackLimit`: длина подачи уровня, но не больше 30 с — в файле клипа дальше паддинг),
 * автоповтора нет. Перемотка — внутри открытой части: на уровне это `seconds`, на ответе
 * раунда (`seconds` = null) — весь фрагмент. Граница держится и через `timeupdate`: в фоновой
 * вкладке `requestAnimationFrame` замирает, а звук идёт дальше. Воспроизведение — только по
 * жесту; повтор и перемотка не запрашивают новую ссылку (файл уже в `Blob`).
 */
import { useEffect, useRef, useState } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { PlayButton } from "@/shared/ui/play-button";
import { SeekBar } from "@/shared/ui/seek-bar";
import { cn } from "@/shared/lib/cn";
import { useMuted } from "../use-muted";
import { MAX_CLIP_S, playbackLimit } from "./audio-limit";

type Props = {
  objectUrl: string;
  /** Длина подачи уровня, с; `null` — открыт весь фрагмент. */
  seconds: number | null;
  /** Подпись кнопки после первого прослушивания (в комнате — «Ещё раз»). */
  replayLabel?: string;
  /** Доля воспроизведения 0..1 от открытой части; `null` — стоит. */
  onProgress?: (fraction: number | null) => void;
  playClassName?: string;
};

export function SeekableAudio({
  objectUrl,
  seconds,
  replayLabel,
  onProgress,
  playClassName,
}: Props) {
  const t = useTranslations();
  const format = useFormatter();
  const audio = useRef<HTMLAudioElement>(null);
  const frame = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [played, setPlayed] = useState(false);
  const [time, setTime] = useState(0);
  // Play ещё не ответил: повторный клик в это время не должен ставить на паузу невключённый звук.
  const starting = useRef(false);
  const [duration, setDuration] = useState(Number.NaN);
  const muted = useMuted();
  const limit = playbackLimit(seconds, duration);

  useEffect(() => {
    const el = audio.current;
    return () => {
      el?.pause();
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [objectUrl]);

  const stop = () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    setPlaying(false);
    onProgress?.(null);
  };

  const tick = () => {
    const el = audio.current;
    if (!el) return;
    const bound = playbackLimit(seconds, el.duration);
    if (el.currentTime >= bound) {
      el.pause(); // конец открытой части: один проход, без цикла
      setTime(bound);
      stop();
      return;
    }
    setTime(el.currentTime);
    onProgress?.(el.currentTime / bound);
    frame.current = requestAnimationFrame(tick);
  };

  const seek = (to: number) => {
    const el = audio.current;
    if (!el) return;
    const bound = playbackLimit(seconds, el.duration);
    const target = Math.min(Math.max(to, 0), bound);
    el.currentTime = target;
    setTime(target);
    if (playing) onProgress?.(target / bound);
  };

  const toggle = async () => {
    const el = audio.current;
    if (!el || starting.current) return;
    if (playing) {
      el.pause();
      stop();
      return;
    }
    // С конца открытой части — сначала; иначе продолжаем с места перемотки.
    if (el.currentTime >= playbackLimit(seconds, el.duration) - 0.05) el.currentTime = 0;
    el.loop = false;
    starting.current = true;
    try {
      await el.play();
      setPlaying(true);
      setPlayed(true);
      frame.current = requestAnimationFrame(tick);
    } catch {
      stop();
    } finally {
      starting.current = false;
    }
  };

  const label =
    played && replayLabel
      ? replayLabel
      : seconds
        ? t("game.listen", { seconds: format.number(seconds, { maximumFractionDigits: 1 }) })
        : t("game.listenPlain");

  return (
    <>
      <audio
        ref={audio}
        src={objectUrl}
        preload="auto"
        loop={false}
        muted={muted}
        onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
        onTimeUpdate={() => {
          const el = audio.current;
          if (el && !el.paused && el.currentTime >= playbackLimit(seconds, el.duration)) {
            el.pause();
            setTime(playbackLimit(seconds, el.duration));
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
        className={cn(playClassName)}
      />
      <SeekBar value={time} max={MAX_CLIP_S} open={limit} onSeek={seek} />
    </>
  );
}
