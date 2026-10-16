/**
 * Игровой экран сессии по сборнику: режим-шаблон ищется в `GET /v1/modes?kind=collection` по
 * `mode.key` снимка — заголовок и лента уровней такие же, как у обычных режимов.
 */
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend, server, signInAsGuest, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { ITEMS } from "../../../e2e/mocks/world";
import { GameScreen } from "./GameScreen";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

setupMockBackend();

const tracks = ITEMS.filter((i) => i.domain === "music").slice(0, 5);

describe("GameScreen — сессия по сборнику", () => {
  it("заголовок и шкала времени уровней — из шаблона режима сборника", async () => {
    const kinds: (string | null)[] = [];
    server.events.on("request:start", ({ request }) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith("/api/game/v1/modes")) kinds.push(url.searchParams.get("kind"));
    });
    const s = world.startSession(signInAsGuest(), world.mode("music.collection.intro")!, null, [], {
      id: "00000000-0000-4000-8000-00000000c011",
      version: 1,
      itemIds: tracks.map((t) => t.id),
    });
    renderWithProviders(<GameScreen sessionId={s.id} />);

    expect(await screen.findByText("Сборник по вступлению")).toBeInTheDocument();
    // Шкала времени из levels[].hint.seconds шаблона: 0,5 с → 1 с.
    expect(await screen.findByTestId("skip")).toHaveTextContent("Пропустить (+0,5 с)");
    expect(screen.getByRole("list", { name: "Подсказки: открыт уровень 1 из 6" })).toBeVisible();
    expect(kinds).toEqual(expect.arrayContaining(["play", "collection"]));
  });
});
