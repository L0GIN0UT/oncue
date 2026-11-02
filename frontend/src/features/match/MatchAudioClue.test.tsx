import { describe, expect, it } from "vitest";
import { MAX_CLIP_S, playbackLimit } from "./MatchAudioClue";

describe("playbackLimit: фрагмент играется один раз до границы уровня", () => {
  it("граница — длина подачи уровня", () => {
    expect(playbackLimit(4, 50)).toBe(4);
  });

  it("файл клипа 50 с (30 с + паддинг под реверс) не звучит дальше 30 с", () => {
    expect(playbackLimit(null, 50)).toBe(MAX_CLIP_S);
    expect(playbackLimit(45, 50)).toBe(MAX_CLIP_S);
  });

  it("файл короче окна — до конца файла, без цикла", () => {
    expect(playbackLimit(30, 12)).toBe(12);
    expect(playbackLimit(null, 12)).toBe(12);
  });

  it("длительность ещё неизвестна (NaN/Infinity) — безопасный потолок", () => {
    expect(playbackLimit(null, Number.NaN)).toBe(MAX_CLIP_S);
    expect(playbackLimit(10, Number.POSITIVE_INFINITY)).toBe(10);
  });
});
