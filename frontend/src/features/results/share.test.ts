import { describe, expect, it, vi } from "vitest";
import type { ResultSummary } from "@/shared/api/types";
import { buildShareText, shareResult } from "./share";

const summary = (patch: Partial<ResultSummary> = {}): ResultSummary => ({
  session_id: "s1",
  mode_key: "music.daily.classic",
  score: 14,
  max_score: 30,
  share_text: "",
  rounds: [
    { index: 0, outcome: "exact", level: 1, points: 6, media_item_id: null },
    { index: 1, outcome: "partial", level: 3, points: 2, media_item_id: null },
    { index: 2, outcome: "miss", level: null, points: 0, media_item_id: null },
    { index: 3, outcome: "gave_up", level: null, points: 0, media_item_id: null },
    { index: 4, outcome: "void", level: null, points: 0, media_item_id: null },
  ],
  ...patch,
});

describe("buildShareText", () => {
  it("без текста сервера собирает сетку по исходам и добавляет ссылку", () => {
    expect(buildShareText(summary(), "Трек дня", "https://oncue.example/")).toBe(
      "Трек дня 14/30\n🟩🟨🟥🟥⬜\nhttps://oncue.example/",
    );
  });

  it("берёт текст сервера и не дублирует ссылку", () => {
    const withLink = summary({ share_text: "OnCue 14/30\n🟩🟨\nhttps://oncue.example/" });
    expect(buildShareText(withLink, "x", "https://oncue.example/")).toBe(withLink.share_text);
    expect(buildShareText(summary({ share_text: "OnCue 14/30" }), "x", "https://o.example/")).toBe(
      "OnCue 14/30\nhttps://o.example/",
    );
  });
});

describe("shareResult", () => {
  it("Web Share API, если доступен", async () => {
    const share = vi.fn(async () => {});
    expect(await shareResult("t", { share, canShare: () => true, writeText: vi.fn() })).toBe(
      "shared",
    );
    expect(share).toHaveBeenCalledWith({ text: "t" });
  });

  it("иначе — буфер обмена", async () => {
    const writeText = vi.fn(async () => {});
    expect(await shareResult("t", { writeText })).toBe("copied");
    expect(writeText).toHaveBeenCalledWith("t");
  });

  it("отмена шаринга пользователем — не ошибка и не копирование", async () => {
    const writeText = vi.fn();
    const share = vi.fn(async () => {
      throw new DOMException("cancel", "AbortError");
    });
    expect(await shareResult("t", { share, writeText })).toBe("cancelled");
    expect(writeText).not.toHaveBeenCalled();
  });

  it("нет ни одного способа — failed", async () => {
    expect(await shareResult("t", {})).toBe("failed");
  });
});
