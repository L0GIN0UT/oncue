/** Контракт компонента подачи загадки (реестр по `clue.kind`). */
import type { LoadedClue } from "../use-clue";

/**
 * Сцена, на которой подаётся загадка: обычная или «чёрный экран» (звук фильма без картинки,
 * ревил — кадр). Выбирается по данным — `clue_type` сессии в реестре сцен (`clueSceneOf`).
 */
export type ClueScene = "stage" | "blackout";

export type ClueStageProps = {
  clue: LoadedClue;
  scene?: ClueScene;
  /** Сообщить долю воспроизведения 0..1 от длительности уровня (только для загадок со временем). */
  onProgress?: (fraction: number | null) => void;
};
