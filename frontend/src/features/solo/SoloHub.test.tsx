import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { signInAsGuest, setupMockBackend } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SoloHub } from "./SoloHub";

setupMockBackend();

describe("SoloHub — домен, вкладки и раскрывающиеся строки", () => {
  it("музыка: быстрые режимы, первая строка раскрыта, пресет выбирается чипом и попадает в ссылку", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    const random = await screen.findByTestId("mode-card-music.random.classic");
    expect(random).toHaveTextContent("10 раундов");
    // Первая строка первой вкладки раскрыта: старт без набора ведёт на режим без пресета.
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
    // У гостя нет рекордов, а день и сетка недели не мешаются в витрину.
    expect(screen.queryByText(/лучший счёт/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("mode-card-film.bracket.weekly")).not.toBeInTheDocument();
  });

  it("кино: марафон с выбором персоны, пары и сетка недели — на своих вкладках", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    await screen.findByTestId("mode-card-music.random.classic");
    await userEvent.click(screen.getByRole("radio", { name: "Кино" }));
    expect(screen.getByTestId("mode-card-film.random.description")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Марафоны" }));
    const marathon = screen.getByTestId("mode-card-film.marathon.director");
    expect(within(marathon).getByRole("link", { name: "Выбрать персону" })).toHaveAttribute(
      "href",
      "/play/film.marathon.director",
    );
    expect(marathon).not.toHaveTextContent("200 раундов");

    await userEvent.click(screen.getByRole("tab", { name: "Пары" }));
    expect(screen.getByTestId("mode-card-film.pair.rating")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Сетка недели" }));
    expect(screen.getByTestId("daily-film.bracket.weekly")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "По сборнику" }));
    expect(screen.getByTestId("solo-collection-catalog")).toHaveAttribute("href", "/collections");
  });

  it("строка раскрывается и сворачивается по клику", async () => {
    signInAsGuest();
    renderWithProviders(<SoloHub initial={null} />);
    const reverse = await screen.findByTestId("mode-card-music.random.reverse");
    const head = within(reverse).getByRole("button", { expanded: false });
    await userEvent.click(head);
    expect(within(reverse).getByRole("link", { name: "Играть" })).toBeInTheDocument();
    await userEvent.click(within(reverse).getByRole("button", { expanded: true }));
    expect(within(reverse).queryByRole("link", { name: "Играть" })).not.toBeInTheDocument();
  });
});
