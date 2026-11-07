"use client";
/**
 * Полный фрагмент трека на ответе раунда (30 с открыты целиком): Play и свободная перемотка.
 * Файл качается в `Blob` один раз, повтор и перемотка новую ссылку не просят. Если финальный
 * ассет — не аудио (кадр фильма), компонент ничего не рисует.
 */
import type { RoundOut } from "@/shared/api/types";
import { SeekableAudio } from "./clues/SeekableAudio";
import { useFinalClue } from "./use-final-clue";

export function RevealAudio({ sessionId, round }: { sessionId: string; round: RoundOut }) {
  const initial = round.reveal?.final_clue ?? null;
  const clip = initial?.kind === "audio" ? initial : null;
  const { objectUrl } = useFinalClue(sessionId, round.index, clip);
  if (!clip || !objectUrl) return null;
  return (
    <div className="flex flex-col items-center gap-3" data-testid="reveal-audio">
      <SeekableAudio
        key={objectUrl}
        objectUrl={objectUrl}
        seconds={null}
        playClassName="size-14"
        inlineSeek
      />
    </div>
  );
}
