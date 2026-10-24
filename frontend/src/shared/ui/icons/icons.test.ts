import { describe, expect, it } from "vitest";
import { THEME_KEYS } from "@/styles/theme";
import { defaultIcons } from "./default";
import { ICON_SETS, iconsFor } from "./index";

describe("иконки по теме", () => {
  it("у каждой темы есть полный набор иконок", () => {
    for (const key of THEME_KEYS) {
      expect(Object.keys(ICON_SETS[key]).sort()).toEqual(Object.keys(defaultIcons).sort());
    }
  });

  it("неизвестный ключ темы — набор default", () => {
    expect(iconsFor("nope")).toBe(ICON_SETS.default);
    expect(iconsFor(undefined)).toBe(ICON_SETS.default);
    expect(iconsFor("default")).toBe(ICON_SETS.default);
  });
});
