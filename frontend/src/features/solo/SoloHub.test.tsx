import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signInAsGuest, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SoloHub } from "./SoloHub";

setupMockBackend();

describe("SoloHub — домен, фильтр, плитки и карточки режимов", () => {
  it("музыка: плитка дейлика, сетка режимов; карточка раскрывается, пресет попадает в ссылку", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    const random = await screen.findByTestId("mode-card-music.random.classic");
    expect(screen.getByTestId("solo-daily-music")).toBeInTheDocument();
    // Карточка свёрнута: описание и старт — после клика.
    expect(within(random).queryByRole("link", { name: "Играть" })).not.toBeInTheDocument();
    await userEvent.click(within(random).getByRole("button", { expanded: false }));
    expect(within(random).getByRole("link", { name: "Играть" })).toHaveAttribute(
      "href",
      "/play/music.random.classic",
    );
    await userEvent.click(within(random).getByRole("button", { name: "2000-е" }));
    expect(within(random).getByRole("link", { name: "Играть" })).toHaveAttribute(
      "href",
      "/play/music.random.classic?preset=y2000s",
    );
    expect(screen.getByTestId("mode-card-music.random.reverse")).toBeInTheDocument();
    // Режимы кино — за переключателем домена.
    expect(screen.queryByTestId("mode-card-film.random.frame")).not.toBeInTheDocument();
    // У гостя нет рекордов и подписи «ещё не играл».
    expect(screen.queryByText(/лучший счёт/)).not.toBeInTheDocument();
    expect(screen.queryByText("ещё не играл")).not.toBeInTheDocument();
  });

  it("кино: фильтр по видам, марафон с выбором персоны, пары и плитка сетки недели", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    await screen.findByTestId("mode-card-music.random.classic");
    await userEvent.click(screen.getByRole("radio", { name: "Кино" }));
    expect(screen.getByTestId("mode-card-film.random.description")).toBeInTheDocument();
    expect(screen.getByTestId("daily-film.bracket.weekly")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Марафоны" }));
    const marathon = screen.getByTestId("mode-card-film.marathon.director");
    expect(within(marathon).getByRole("link", { name: "Выбрать персону" })).toHaveAttribute(
      "href",
      "/play/film.marathon.director",
    );
    expect(screen.queryByTestId("mode-card-film.random.description")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Пары" }));
    expect(screen.getByTestId("mode-card-film.pair.rating")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Все" }));
    expect(screen.getByTestId("mode-card-film.random.description")).toBeInTheDocument();
  });

  it("карточка раскрывается и сворачивается по клику", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    const reverse = await screen.findByTestId("mode-card-music.random.reverse");
    await userEvent.click(within(reverse).getByRole("button", { expanded: false }));
    expect(within(reverse).getByRole("link", { name: "Играть" })).toBeInTheDocument();
    await userEvent.click(within(reverse).getByRole("button", { expanded: true }));
    expect(within(reverse).queryByRole("link", { name: "Играть" })).not.toBeInTheDocument();
  });
});
