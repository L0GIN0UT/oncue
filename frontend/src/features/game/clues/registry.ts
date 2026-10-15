/**
 * Реестр подачи загадок по `clue.kind` из ответа game. Новый вид подачи — новая запись,
 * без ветвлений по `clue_type` в экране игры. Подача `text` (описание, эмодзи) появится в
 * потоке D этапа 3a: до тех пор вида нет в реестре и экран его не рисует.
 */
import type { ComponentType } from "react";
import type { ClueKind } from "@/shared/api/types";
import { AudioClue } from "./AudioClue";
import { ImageClue } from "./ImageClue";
import type { ClueStageProps } from "./types";

export const clueRegistry: Partial<Record<ClueKind, ComponentType<ClueStageProps>>> = {
  audio: AudioClue,
  image: ImageClue,
};
