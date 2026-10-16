/**
 * Туры сетки по данным снимка: `progress.stage` раунда — размер тура (32, 16 … 2 участника).
 * Клиент не знает размер сетки заранее: туры выводятся из тура первого раунда.
 */
import type { RoundOut } from "@/shared/api/types";

/** Туры от первого до финала: `[32, 16, 8, 4, 2]` для сетки из 32. */
export function bracketStages(first: number | null | undefined): number[] {
  const stages: number[] = [];
  if (!first || first < 2) return stages;
  for (let s = first; s >= 2; s = Math.floor(s / 2)) stages.push(s);
  return stages;
}

/** Подпись тура: ключ i18n и параметры (`1/16 финала`, `Полуфинал`, `Финал`). */
export function stageLabel(
  stage: number,
):
  | { key: "game.bracket.final" }
  | { key: "game.bracket.semifinal" }
  | { key: "game.bracket.fraction"; denominator: number } {
  if (stage <= 2) return { key: "game.bracket.final" };
  if (stage === 4) return { key: "game.bracket.semifinal" };
  return { key: "game.bracket.fraction", denominator: stage / 2 };
}

/** Номер пары внутри тура текущего раунда (1-based) и число пар в туре. */
export function pairInStage(
  rounds: readonly RoundOut[],
  current: RoundOut,
): { pair: number; pairs: number } | null {
  const stage = current.progress?.stage;
  if (!stage) return null;
  const before = rounds.filter((r) => r.index < current.index && r.progress?.stage === stage);
  return { pair: before.length + 1, pairs: Math.max(1, Math.floor(stage / 2)) };
}
