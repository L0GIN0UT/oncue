import { describe, expect, it } from "vitest";
import { checkEmoji, graphemes } from "./emoji";

describe("checkEmoji", () => {
  it("графемы, а не кодовые точки: флаг, семья и тон кожи — по одной", () => {
    expect(graphemes("🇷🇺👨‍👩‍👧👍🏽🏙️")).toHaveLength(4);
  });

  it("6..12 эмодзи — ок; меньше и больше — по лимитам", () => {
    expect(checkEmoji("🚀🌌⏳🌽🪐📚", 6, 12)).toBe("ok");
    expect(checkEmoji("🚀🌌", 6, 12)).toBe("too_short");
    expect(checkEmoji("🚀".repeat(13), 6, 12)).toBe("too_long");
    expect(checkEmoji("  ", 6, 12)).toBe("empty");
  });

  it("буквы, цифры и пробелы внутри — не эмодзи", () => {
    expect(checkEmoji("🚀🌌⏳🌽🪐a", 1, 12)).toBe("not_emoji");
    expect(checkEmoji("🚀 🌌⏳🌽🪐📚", 1, 12)).toBe("not_emoji");
    expect(checkEmoji("1🚀🌌⏳🌽🪐", 1, 12)).toBe("not_emoji");
    expect(checkEmoji("1️⃣🚀🌌⏳🌽🪐", 1, 12)).toBe("ok");
  });
});
