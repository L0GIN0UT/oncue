import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { HomeProgress } from "./HomeProgress";

setupMockBackend();

describe("HomeProgress", () => {
  it("пользователь: уровень, серия, колесо и открытые задания", async () => {
    signInAsUser();
    renderWithProviders(<HomeProgress initial={null} />);

    expect(await screen.findByTestId("home-level")).toHaveTextContent("Уровень 7");
    expect(screen.getByTestId("home-streak")).toHaveTextContent("Серия: 5 дней");
    expect(screen.getByTestId("home-quests")).toHaveTextContent("1 задание открыто");
    expect(screen.getByTestId("home-wheel")).toHaveTextContent("Колесо доступно");
    expect(screen.getByRole("link", { name: "В профиль" })).toHaveAttribute("href", "/profile");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "24");
  });

  it("колесо уже крутили — так и сказано", async () => {
    signInAsUser();
    world.progression.spun.set("user.00000000-0000-4000-8000-000000000901", {
      day: "2026-10-23",
      sector_key: "empty",
      prize: { kind: "none", amount: 0 },
      spun_at: "2026-10-23T08:00:00Z",
      repeated: false,
    });
    renderWithProviders(<HomeProgress initial={null} />);
    expect(await screen.findByTestId("home-wheel")).toHaveTextContent("уже крутили");
  });

  it("блок погас — короткая заглушка", async () => {
    signInAsUser();
    world.scenario.progressDown = ["home"];
    renderWithProviders(<HomeProgress initial={null} />);
    expect(await screen.findByTestId("home-progress-unavailable")).toHaveTextContent(
      "Появится позже",
    );
  });

  it("гость и аноним: карточки нет", async () => {
    signInAsGuest();
    renderWithProviders(<HomeProgress initial={null} />);
    await waitFor(() => expect(world.progression.spinCalls).toBe(0));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("home-progress")).toBeNull();
  });
});
