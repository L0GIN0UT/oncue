import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { RoundRevealDelta } from "@/shared/realtime";
import { RoundRevealPanel } from "./RoundRevealPanel";

setupMockBackend();

const artwork = {
  url: "https://m/cover.jpg",
  expires_at: "2099-01-01T00:00:00Z",
  mime: "image/jpeg",
};

const reveal = (extra: Partial<RoundRevealDelta>) =>
  ({
    round_index: 0,
    media_item_id: "m1",
    title: "Nevermind",
    year: 1991,
    persons: [],
    final_clue: null,
    results: [],
    ...extra,
  }) as RoundRevealDelta;

describe("ревил комнаты: обложка и постер", () => {
  it("обложка из artwork", () => {
    renderWithProviders(
      <RoundRevealPanel code="K7MQ2XWP" reveal={reveal({ artwork })} players={[]} domain="music" />,
    );
    expect(screen.getByRole("img", { name: "Обложка «Nevermind»" })).toHaveAttribute(
      "src",
      artwork.url,
    );
  });

  it("artwork = null — заглушка, ревил на месте", () => {
    renderWithProviders(
      <RoundRevealPanel
        code="K7MQ2XWP"
        reveal={reveal({ artwork: null })}
        players={[]}
        domain="film"
      />,
    );
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByTestId("reveal-title")).toHaveTextContent("Nevermind");
  });
});
