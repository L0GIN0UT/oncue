import { describe, expect, it } from "vitest";
import type { ModeOut } from "@/shared/api/types";
import { collectionLobbyTemplate } from "./PlayWithFriends";

const mode = (patch: Partial<ModeOut>): ModeOut =>
  ({
    key: "k",
    domain: "film",
    source_type: "random",
    online: { kind: "lobby", ranked: false },
    ...patch,
  }) as ModeOut;

describe("collectionLobbyTemplate", () => {
  it("лобби по сборнику своего домена; дуэли, рейтинговые и чужой домен — мимо", () => {
    const modes = [
      mode({
        key: "film.duel.frame",
        online: { kind: "duel", ranked: false } as ModeOut["online"],
      }),
      mode({ key: "music.lobby.collection", domain: "music", source_type: "collection" }),
      mode({
        key: "film.ranked.collection",
        source_type: "collection",
        online: { kind: "lobby", ranked: true } as ModeOut["online"],
      }),
      mode({ key: "film.lobby.collection", source_type: "collection" }),
    ];
    expect(collectionLobbyTemplate(modes, "film")?.key).toBe("film.lobby.collection");
    expect(collectionLobbyTemplate(modes.slice(0, 1), "film")).toBeNull();
    expect(collectionLobbyTemplate([], "music")).toBeNull();
  });
});
