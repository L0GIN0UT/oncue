/**
 * Разметка раунда формата `pick` по данным снимка: какие единицы показаны открыто (`shown`),
 * какие варианты можно выбрать (`options`) и какая карточка относится к какому варианту.
 * Клиент не знает, пара это или сетка: вариант с открытой единицей — карточка с кадром, без
 * неё — кнопка с подписью (`label_key`), открытая единица без варианта — опорная.
 */
import type { OptionOut, RoundOut, ShownItem } from "@/shared/api/types";

export type PickCard = { option: OptionOut; shown: ShownItem | null };

export type PickLayout = {
  /** Открытые единицы без варианта: опорная единица «больше/меньше» (с метрикой). */
  reference: ShownItem[];
  cards: PickCard[];
};

export function pickLayout(round: Pick<RoundOut, "options" | "shown">): PickLayout {
  const shown = round.shown ?? [];
  const options = round.options ?? [];
  return {
    reference: shown.filter((s) => !s.option_id),
    cards: options.map((option) => ({
      option,
      shown: shown.find((s) => s.option_id === option.option_id) ?? null,
    })),
  };
}

/**
 * Нужна ли раунду загадка уровня (`POST .../clue`): не нужна, если каждый вариант уже показан
 * своей открытой единицей (сетка — выбор между двумя кадрами). Классический раунд и «больше/
 * меньше» (варианты — подписи) загадку показывают.
 */
export function needsLevelClue(round: Pick<RoundOut, "options" | "shown">): boolean {
  const { cards } = pickLayout(round);
  return !(cards.length > 0 && cards.every((c) => c.shown !== null));
}

/**
 * Вариант по клавише: цифры `1…9` — по порядку, `←`/`→` — первый и второй из двух вариантов.
 * `null` — клавиша не выбирает.
 */
export function optionForKey(key: string, options: readonly OptionOut[]): string | null {
  if (/^[1-9]$/.test(key)) return options[Number(key) - 1]?.option_id ?? null;
  if (options.length !== 2) return null;
  if (key === "ArrowLeft") return options[0]!.option_id;
  if (key === "ArrowRight") return options[1]!.option_id;
  return null;
}
