import { describe, expect, it, vi } from "vitest";
import { createTranslator } from "next-intl";
import messages from "../../../messages/ru.json";
import type { ResultSummary } from "@/shared/api/types";
import { buildShareText, shareResult, type ShareContext, type Translate } from "./share";

const t = createTranslator({ locale: "ru", messages }) as unknown as Translate;
const ctx = (title: string, homeUrl: string): ShareContext => ({ title, homeUrl, t });

const summary = (patch: Partial<ResultSummary> = {}): ResultSummary => ({
  session_id: "s1",
  mode_key: "music.daily.classic",
  score: 14,
  max_score: 30,
  share_text: "",
  presentation: "score",
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
    expect(buildShareText(summary(), ctx("Трек дня", "https://oncue.example/"))).toBe(
      "Трек дня 14/30\n🟩🟨🟥🟥⬜\nhttps://oncue.example/",
    );
  });

  it("берёт текст сервера и не дублирует ссылку", () => {
    const withLink = summary({ share_text: "OnCue 14/30\n🟩🟨\nhttps://oncue.example/" });
    expect(buildShareText(withLink, ctx("x", "https://oncue.example/"))).toBe(withLink.share_text);
    expect(
      buildShareText(summary({ share_text: "OnCue 14/30" }), ctx("x", "https://o.example/")),
    ).toBe("OnCue 14/30\nhttps://o.example/");
  });
});

describe("buildShareText по presentation", () => {
  it("серия: длина серии, новый рекорд и клетки; текст сервера-сетки не используется", () => {
    const streak = summary({
      presentation: "streak",
      score: 7,
      max_score: 7,
      share_text: "OnCue film.pair.rating\n7/7\n🟩",
      record: { best: 7, is_new: true, percentile: 91 },
      rounds: [
        { index: 0, outcome: "exact", level: 1, points: 1, media_item_id: null },
        { index: 1, outcome: "miss", level: null, points: 0, media_item_id: null },
      ],
    });
    expect(buildShareText(streak, ctx("Больше или меньше", "https://o.example/"))).toBe(
      "Больше или меньше\nМоя серия: 7 🔥\nНовый личный рекорд!\n🟩🟥\nhttps://o.example/",
    );
  });

  it("серия без рекорда — без строки рекорда", () => {
    const streak = summary({ presentation: "streak", score: 0, rounds: [], record: null });
    expect(buildShareText(streak, ctx("Пара", "https://o.example/"))).toBe(
      "Пара\nМоя серия: 0 🔥\nhttps://o.example/",
    );
  });

  it("сетка: чемпион игрока", () => {
    const bracket = summary({
      presentation: "bracket",
      score: 0,
      max_score: 0,
      bracket: { champion: "Сталкер", picks: [] },
    });
    expect(buildShareText(bracket, ctx("Сетка недели", "https://o.example/"))).toBe(
      "Сетка недели\nМой чемпион: «Сталкер» 🏆\nhttps://o.example/",
    );
  });

  it("сетка без чемпиона — как сумма очков", () => {
    const bracket = summary({ presentation: "bracket", bracket: null, share_text: "OnCue" });
    expect(buildShareText(bracket, ctx("x", "https://o.example/"))).toBe(
      "OnCue\nhttps://o.example/",
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
