/**
 * Реестр подачи загадок по `clue.kind` из ответа game. Новый вид подачи — новая запись,
 * без ветвлений по `clue_type` в экране игры.
 */
import type { ComponentType } from "react";
import type { ClueKind } from "@/shared/api/types";
import { AudioClue } from "./AudioClue";
import { ImageClue } from "./ImageClue";
import type { ClueStageProps } from "./types";

export const clueRegistry: Record<ClueKind, ComponentType<ClueStageProps>> = {
  audio: AudioClue,
  image: ImageClue,
};
