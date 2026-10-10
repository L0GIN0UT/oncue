/** Контракт компонента подачи загадки (реестр по `clue.kind`). */
import type { LoadedClue } from "../use-clue";

export type ClueStageProps = {
  clue: LoadedClue;
  /** Сообщить долю воспроизведения 0..1 от длительности уровня (только для загадок со временем). */
  onProgress?: (fraction: number | null) => void;
};
