import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend, signInAsGuest, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { RevealAudio } from "./RevealAudio";

setupMockBackend();

function finished(modeKey: string) {
  const s = world.startSession(signInAsGuest(), world.mode(modeKey)!, null);
  world.giveUp(s, s.rounds[0]!);
  return { s, round: world.snapshot(s).rounds[0]! };
}

describe("RevealAudio: ответ раунда", () => {
  it("музыка: плеер с перемоткой по всему 30-секундному фрагменту", async () => {
    const { s, round } = finished("music.random.classic");
    renderWithProviders(<RevealAudio sessionId={s.id} round={round} />);
    expect(await screen.findByTestId("reveal-audio")).toBeInTheDocument();
    const bar = screen.getByRole("slider", { name: "Перемотка" });
    expect(bar).toHaveAttribute("aria-valuemax", "30");
  });

  it("кадр фильма — плеера нет", async () => {
    const { s, round } = finished("film.random.frame");
    renderWithProviders(<RevealAudio sessionId={s.id} round={round} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("reveal-audio")).not.toBeInTheDocument();
  });
});
