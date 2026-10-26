/** Значения токенов `default` совпадают со спецификацией v3 (`design/mockups/SPEC-v3.md`). */
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { parseColorTokens } from "./contrast";
import { THEME_META } from "./theme";

const tokens = parseColorTokens(
  readFileSync(path.join(import.meta.dirname, "themes", "default.css"), "utf8"),
);

const SPEC: Record<string, string> = {
  night: "#1a1a1a",
  stage: "#242424",
  "stage-raised": "#2e2e2e",
  rule: "#474747",
  "rule-strong": "#6a6a6a",
  ink: "#f2f2f2",
  "ink-muted": "#ababab",
  "ink-dim": "#6a6a6a",
  "ink-soft": "#d9d9d9",
  music: "#f2b13b",
  exact: "#9be39f",
  "exact-fill": "#27592f",
  "exact-line": "#357a42",
  partial: "#f2b13b",
  "partial-fill": "#5a4516",
  "partial-line": "#7a5f1e",
  miss: "#f2a0a0",
  "miss-fill": "#552828",
  "miss-line": "#7a3838",
  skip: "#ababab",
  "skip-fill": "#2e2e2e",
  "skip-line": "#474747",
  "tier-steel": "#9aa4ae",
  "tier-bronze": "#b5703a",
  "tier-silver": "#c9ccd6",
  "tier-gold": "#f2b13b",
  "tier-diamond": "#8fd3ff",
};

describe("токены v3", () => {
  it.each(Object.entries(SPEC))("%s = %s", (name, value) => {
    expect(tokens[name]).toBe(value);
  });

  it("themeColor default совпадает с фоном", () => {
    expect(THEME_META.default.themeColor).toBe(SPEC.night);
  });
});
