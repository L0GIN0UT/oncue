/**
 * Режимы-шаблоны сборников (source `collection`, D4) для конструктора и фильтра каталога.
 *
 * Список — из game `GET /v1/modes?kind=collection` (шаблоны домена, без учёта конкретного
 * сборника); переводы нужны только для названий (`title_key`). Режим черновика или фильтра,
 * которого нет в списке (снят с публикации), остаётся выбранным — с ключом вместо названия.
 */
import type { Domain } from "@/shared/api/types";
import { useModeList } from "@/features/game/modes";

export type CollectionModeOption = { key: string; titleKey: string | null };

/** Шаблоны сборника домена (без `domain` — всех доменов); `current` добавляется, если его нет. */
export function useCollectionModes(
  domain: Domain | null | undefined,
  current?: string | null,
): CollectionModeOption[] {
  const list = useModeList(domain ?? undefined, "collection");
  const options = (list.data ?? []).map((m) => ({ key: m.key, titleKey: m.title_key }));
  return current && !options.some((m) => m.key === current)
    ? [...options, { key: current, titleKey: null }]
    : options;
}
