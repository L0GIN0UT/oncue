/**
 * Реестры подачи загадок (D12 плана этапа 3a): компонент — по `clue.kind` из ответа game
 * (`audio`, `image`, `text`), сцена — по `clue_type` сессии. Новый вид подачи — новая запись,
 * без ветвлений по ключу режима или `source_type` в экране игры.
 */
import type { ComponentType } from "react";
import type { ClueKind } from "@/shared/api/types";
import { AudioClue } from "./AudioClue";
import { ImageClue } from "./ImageClue";
import { TextClue } from "./TextClue";
import type { ClueScene, ClueStageProps } from "./types";

export const clueRegistry: Record<ClueKind, ComponentType<ClueStageProps>> = {
  audio: AudioClue,
  image: ImageClue,
  text: TextClue,
};

/**
 * Сцены по типу загадки ядра. Тип без записи подаётся на обычной сцене. `audio_blackout` —
 * звук фильма на чёрном экране (ADR 0026): картинка появляется только в ревиле.
 */
export const clueScenes: Readonly<Record<string, ClueScene>> = {
  audio_blackout: "blackout",
};

export function clueSceneOf(clueType: string | null | undefined): ClueScene {
  return (clueType && clueScenes[clueType]) || "stage";
}
