import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { FRIENDS_REFRESH_MS } from "./config";
import { FriendsView } from "./FriendsView";

setupMockBackend();

beforeEach(() => {
  vi.useFakeTimers({
    shouldAdvanceTime: true,
    toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"],
  });
});
afterEach(() => vi.useRealTimers());

describe("FriendsView: новые заявки без перезагрузки", () => {
  it("счётчик на вкладке и список обновляются раз в период", async () => {
    signInAsUser();
    renderWithProviders(<FriendsView />);
    expect(await screen.findByText("Заявки (1)")).toBeInTheDocument();

    world.social.incoming.push({
      id: "0192b000-0000-7000-8001-0000000000bb",
      user_id: "0192b000-0000-7000-8003-0000000000cc",
      nickname: "Елена",
      created_at: "2026-10-24T10:00:00Z",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FRIENDS_REFRESH_MS + 500);
    });
    expect(await screen.findByText("Заявки (2)")).toBeInTheDocument();
  });
});
