/**
 * Реестр презентеров итога по `presentation` (объявляет Scorer ядра, D12): `score` — сумма
 * очков, `streak` — серия, `bracket` — чемпион сетки. Новая подача — новая запись.
 */
import type { Presentation } from "@/shared/api/types";
import { bracketPresenter } from "./bracket";
import { scorePresenter } from "./score";
import { streakPresenter } from "./streak";
import type { Presenter } from "./types";

export const presenterRegistry: Record<Presentation, Presenter> = {
  score: scorePresenter,
  streak: streakPresenter,
  bracket: bracketPresenter,
};
