import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { signInAsGuest, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SoloHub } from "./SoloHub";

setupMockBackend();

describe("SoloHub — витрина по блокам bff", () => {
  it("группы режимов, карточки mode-card-<key>, сетка недели среди дня, без рекордов у гостя", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    const marathon = await screen.findByTestId("mode-card-film.marathon.director");
    // Выбор персоны — по `start.subject`, не по ключу режима.
    expect(within(marathon).getByRole("link", { name: "Выбрать персону" })).toHaveAttribute(
      "href",
      "/play/film.marathon.director",
    );
    expect(marathon).not.toHaveTextContent("200 раундов");
    const random = screen.getByTestId("mode-card-music.random.classic");
    expect(random).toHaveTextContent("10 раундов");
    expect(within(random).getByRole("link", { name: "2000-е" })).toHaveAttribute(
      "href",
      "/play/music.random.classic?preset=y2000s",
    );
    for (const key of [
      "music.marathon.artist",
      "film.pair.rating",
      "music.pair.popularity",
      "film.random.description",
      "film.random.blackout",
      "film.random.soundtrack",
    ])
      expect(screen.getByTestId(`mode-card-${key}`)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Марафоны" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Больше или меньше" })).toBeInTheDocument();
    // Плановые режимы — не в витрине, а среди наборов дня.
    expect(screen.queryByTestId("mode-card-film.bracket.weekly")).not.toBeInTheDocument();
    expect(screen.getByTestId("daily-film.bracket.weekly")).toBeInTheDocument();
    expect(screen.queryByText("Ваши рекорды")).not.toBeInTheDocument();
  });
});
