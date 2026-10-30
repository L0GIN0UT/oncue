import { describe, expect, it } from "vitest";
import type { EventFindCard } from "@/shared/api/types";
import { openSpotFor, playRoute, sortedFinds, trailPaths } from "./logic";

const find = (over: Partial<EventFindCard> & { key: string }): EventFindCard => ({
  number: 1,
  title: "t",
  x_pct: 1,
  y_pct: 1,
  final: false,
  state: "open",
  diary: "d",
  reward: { xp: 1, verses: 1 },
  play_page: null,
  trigger: { type: "ui_spot", page_key: "shop" },
  found_at: null,
  ...over,
});

describe("playRoute", () => {
  it.each([
    ["solo", "/solo"],
    ["online", "/online"],
    ["wheel", "/profile"],
    ["home", "/"],
  ])("%s → %s", (page, route) => {
    expect(playRoute(page)).toBe(route);
  });

  it.each([null, undefined, "", "unknown", "__proto__", "constructor"])(
    "неизвестное (%s) — без кнопки",
    (page) => {
      expect(playRoute(page)).toBeNull();
    },
  );
});

describe("openSpotFor", () => {
  const page = (finds: EventFindCard[], status: "active" | "finished" = "active") => ({
    status,
    finds,
  });

  it("находит открытый ui_spot своей страницы", () => {
    expect(openSpotFor(page([find({ key: "a" })]), "shop")?.key).toBe("a");
  });

  it("чужая страница, найденная, закрытая и server-находки не подходят", () => {
    const finds = [
      find({ key: "other", trigger: { type: "ui_spot", page_key: "home" } }),
      find({ key: "done", state: "found" }),
      find({ key: "locked", state: "locked" }),
      find({ key: "srv", trigger: { type: "server", metric: "duel_win" } }),
    ];
    expect(openSpotFor(page(finds), "shop")).toBeNull();
  });

  it("завершённое событие и отсутствие данных — ничего", () => {
    expect(openSpotFor(page([find({ key: "a" })], "finished"), "shop")).toBeNull();
    expect(openSpotFor(null, "shop")).toBeNull();
  });
});

describe("sortedFinds", () => {
  it("сортирует по номеру, не меняя исходный массив", () => {
    const src = [find({ key: "b", number: 2 }), find({ key: "a", number: 1 })];
    expect(sortedFinds(src).map((f) => f.key)).toEqual(["a", "b"]);
    expect(src[0]!.key).toBe("b");
  });
});

describe("trailPaths", () => {
  const at = (n: number, state: EventFindCard["state"]) =>
    find({ key: `f${n}`, number: n, x_pct: n * 10, y_pct: n * 5, state });

  it("сплошная линия — до последней найденной, пунктир — от неё до конца", () => {
    const finds = [at(1, "found"), at(2, "found"), at(3, "open"), at(4, "locked")];
    expect(trailPaths(finds)).toEqual({
      found: "M 10 5 L 20 10",
      rest: "M 20 10 L 30 15 L 40 20",
    });
  });

  it("ничего не найдено — только пунктир с первой точки; найдено всё — только сплошная", () => {
    expect(trailPaths([at(1, "open"), at(2, "locked")])).toEqual({
      found: "",
      rest: "M 10 5 L 20 10",
    });
    expect(trailPaths([at(1, "found"), at(2, "found")])).toEqual({
      found: "M 10 5 L 20 10",
      rest: "",
    });
  });

  it("одна точка линии не даёт", () => {
    expect(trailPaths([at(1, "open")])).toEqual({ found: "", rest: "" });
  });
});
