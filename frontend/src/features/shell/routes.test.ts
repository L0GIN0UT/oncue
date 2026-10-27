import { describe, expect, it } from "vitest";
import { activeNav, isGameRoute, isWideRoute } from "./routes";

describe("isGameRoute", () => {
  it.each([
    "/play/music.daily.classic",
    "/play/s/abc",
    "/match/42",
    "/room/K7MQ2XWP",
    "/play",
    "/results/abc",
  ])("%s — игровой", (p) => expect(isGameRoute(p)).toBe(true));
  it.each(["/", "/solo", "/resultsboard", "/playlists", "/roommates", "/lobbies"])(
    "%s — обычный",
    (p) => expect(isGameRoute(p)).toBe(false),
  );
});

describe("isWideRoute", () => {
  it("главная и экраны v3 — широкие, остальные пока в колонке", () => {
    for (const p of ["/", "/solo", "/online", "/lobbies", "/collections", "/profile", "/shop"])
      expect(isWideRoute(p)).toBe(true);
    expect(isWideRoute("/leaderboards")).toBe(true);
    expect(isWideRoute("/results/abc")).toBe(true);
    expect(isWideRoute("/players/abc")).toBe(true);
    expect(isWideRoute("/me/security")).toBe(false);
    expect(isWideRoute("/friends")).toBe(false);
  });
});

describe("activeNav", () => {
  it("пункт по разделу, вложенные страницы считаются разделом", () => {
    expect(activeNav("/")).toBe("home");
    expect(activeNav("/solo")).toBe("solo");
    expect(activeNav("/online")).toBe("online");
    expect(activeNav("/lobbies")).toBe("online");
    expect(activeNav("/collections/abc/edit")).toBe("collections");
  });
  it("профиль, магазин, лидеры — ни один пункт", () => {
    expect(activeNav("/profile")).toBeNull();
    expect(activeNav("/shop")).toBeNull();
    expect(activeNav("/leaderboards")).toBeNull();
  });
});
