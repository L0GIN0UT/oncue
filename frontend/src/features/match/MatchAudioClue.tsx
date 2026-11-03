"use client";
/**
 * Аудио-загадка комнаты. Тот же плеер с перемоткой, что в соло (`SeekableAudio`): фрагмент
 * играется один проход и останавливается на границе уровня (`meta.seconds`, но не дальше
 * {@link MAX_CLIP_S}: файл клипа длиннее — 30 с зациклены до 50 с под реверс, и без границы
 * звучал бы «повтор», пункт 57); перемотка — только внутри открытой части. Автоповтора нет,
 * «Ещё раз» — по желанию игрока.
 *
 * Готовность фрагмента (для общего старта таймера) определяет экран: Blob уже скачан целиком.
 */
import { useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { SeekableAudio } from "@/features/game/clues/SeekableAudio";
import type { ClueStageProps } from "@/features/game/clues/types";

export { MAX_CLIP_S, playbackLimit } from "@/features/game/clues/audio-limit";

export function MatchAudioClue({ clue, onProgress, scene = "stage" }: ClueStageProps) {
  const t = useTranslations();
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
      <SeekableAudio
        key={clue.objectUrl}
        objectUrl={clue.objectUrl}
        seconds={clue.meta.seconds ?? null}
        replayLabel={t("match.replay")}
        onProgress={onProgress}
      />
      {scene === "blackout" && <p className="text-sm text-white/70">{t("game.blackoutHint")}</p>}
    </div>
  );
}
