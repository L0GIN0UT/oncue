/**
 * Разметка уровней загадки по подсказкам конфига режима (`levels[].hint`, `GET /v1/modes`).
 * Клиент не знает типов загадок: шкала строится по полям подсказки, а не по `clue_type`.
 */
import type { LevelOut, RoundOut } from "@/shared/api/types";

const secondsOf = (level: LevelOut): number | null => {
  const v = level.hint.seconds;
  return typeof v === "number" && v > 0 ? v : null;
};

/** Есть ли у всех уровней длительность (шкала времени, а не равные шаги). */
export function isTimed(levels: LevelOut[]): boolean {
  return levels.length > 0 && levels.every((l) => secondsOf(l) !== null);
}

/** Веса отрезков шкалы: прирост длительности на уровень либо равные доли. */
export function tapeWeights(levels: LevelOut[], levelsCount: number): number[] {
  if (levels.length === levelsCount && isTimed(levels)) {
    const secs = levels.map((l) => secondsOf(l)!);
    return secs.map((s, i) => Math.max(s - (secs[i - 1] ?? 0), 0.001));
  }
  return Array.from({ length: levelsCount }, () => 1);
}

/** Сколько секунд добавит «Пропустить» на уровне `level` (1-based); `null` — шкала без времени. */
export function skipGainSeconds(levels: LevelOut[], level: number): number | null {
  if (!isTimed(levels)) return null;
  const now = levels[level - 1];
  const next = levels[level];
  if (!now || !next) return null;
  return secondsOf(next)! - secondsOf(now)!;
}

/** Длительность фрагмента уровня, если она есть. */
export function levelSeconds(levels: LevelOut[], level: number): number | null {
  const l = levels[level - 1];
  return l ? secondsOf(l) : null;
}

export type RoundOutcome = "exact" | "partial" | "miss" | "void" | "active" | "pending";

/** Исход раунда для индикатора: по статусу и попыткам (очки считает сервер). */
export function roundOutcome(round: RoundOut): RoundOutcome {
  if (round.status === "void") return "void";
  if (round.status === "pending") return "pending";
  if (round.status === "active") return "active";
  if (round.guesses.some((g) => g.result === "exact")) return "exact";
  if ((round.points ?? 0) > 0 && round.guesses.some((g) => g.result === "partial"))
    return "partial";
  return "miss";
}
