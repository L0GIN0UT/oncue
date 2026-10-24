import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { PLAYERS } from "../../../e2e/mocks/progression";
import { PlayerView } from "./PlayerView";

setupMockBackend();

const PLAYER = PLAYERS[0]!;

describe("PlayerView", () => {
  it("уровень, опыт и бейджи игрока из данных API", async () => {
    signInAsUser();
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);

    expect(await screen.findByTestId("player-level")).toHaveTextContent("Уровень 12");
    expect(screen.getByTestId("player-xp")).toHaveTextContent("7");
    expect(screen.getByTestId("player-badge-first_win")).toHaveTextContent("Первая победа");
    expect(screen.getByTestId("player-badge-streak_7")).toHaveTextContent("Неделя подряд");
  });

  it("оформление игрока: титул под ником; блок appearance недоступен — страница цела", async () => {
    signInAsUser();
    const view = renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-title")).toHaveTextContent("Чемпион");
    view.unmount();

    world.scenario.progressDown = ["appearance"];
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-level")).toBeInTheDocument();
    expect(screen.getByTestId("player-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("player-title")).toBeNull();
  });

  it("нет такого игрока — понятное сообщение", async () => {
    signInAsUser();
    renderWithProviders(<PlayerView userId="00000000-0000-4000-8000-0000000009ff" />);
    expect(await screen.findByTestId("player-error")).toHaveTextContent("Такого игрока нет");
  });

  it("progression недоступен — «появится позже»", async () => {
    signInAsUser();
    world.scenario.progressDown = ["player"];
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-unavailable")).toHaveTextContent("Появится позже");
  });

  it("аноним — вход, гость — регистрация", async () => {
    const view = renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByRole("link", { name: "Войти" })).toHaveAttribute("href", "/login");
    view.unmount();

    signInAsGuest();
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByRole("link", { name: "Зарегистрироваться" })).toHaveAttribute(
      "href",
      "/register",
    );
  });
});
