import { describe, expect, it } from "vitest";
import type { ShelfOut } from "@/shared/api/types";
import { exclusiveRefs, optimisticShelves, refPathSerializer } from "./api";

const shelf = (ref: string, exclusive: string[] = []): ShelfOut => ({
  ref,
  kind: "system",
  key: ref.split(":")[1] ?? null,
  domains: ["film"],
  exclusive_with: exclusive,
  items_count: 0,
});

describe("медиатека: чистые функции", () => {
  it("ref системной полки в пути — с двоеточием, остальное кодируется", () => {
    expect(
      refPathSerializer("/v1/shelves/{shelf_ref}/items/{media_item_id}", {
        shelf_ref: "system:watch_later",
        media_item_id: "a/b",
      }),
    ).toBe("/v1/shelves/system:watch_later/items/a%2Fb");
  });

  it("exclusive_with: ключи конфига → ref полок", () => {
    expect(exclusiveRefs(shelf("system:watched", ["watch_later"]))).toEqual(["system:watch_later"]);
  });

  it("оптимистичные полки: сохранение снимает исключающие, удаление — только саму полку", () => {
    const watched = shelf("system:watched", ["watch_later"]);
    expect(optimisticShelves(["system:watch_later", "system:favorite"], watched, true)).toEqual([
      "system:favorite",
      "system:watched",
    ]);
    expect(optimisticShelves(["system:watched", "system:favorite"], watched, false)).toEqual([
      "system:favorite",
    ]);
  });
});
