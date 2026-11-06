import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { CatalogView } from "./CatalogView";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/collections",
  useSearchParams: () => new URLSearchParams("sort=new"),
}));

setupMockBackend();

const cardsIn = (el: HTMLElement) => el.querySelectorAll('[data-testid^="collection-card-"]');

describe("CatalogView: обложки ленты (collections напрямую)", () => {
  beforeEach(() => signInAsUser());

  it("после загрузки страницы один запрос covers, обложки подставлены в карточки", async () => {
    renderWithProviders(<CatalogView />);
    const results = await screen.findByTestId("collections-results");
    await waitFor(() =>
      expect(
        results.querySelectorAll('[data-testid="collection-cover-image"]').length,
      ).toBeGreaterThan(0),
    );
    expect(world.ugc.coverCalls).toHaveLength(1);
    expect(world.ugc.coverCalls[0]).toHaveLength(cardsIn(results).length);
  });

  it("covers недоступна: карточки на месте с пресетами, ошибки в интерфейсе нет", async () => {
    world.ugc.scenario.coversFail = true;
    renderWithProviders(<CatalogView />);
    const results = await screen.findByTestId("collections-results");
    await waitFor(() => expect(world.ugc.coverCalls).toHaveLength(1));
    expect(cardsIn(results).length).toBeGreaterThan(0);
    expect(screen.queryAllByTestId("collection-cover-image")).toHaveLength(0);
    expect(world.ugc.coverCalls).toHaveLength(1);
  });
});
