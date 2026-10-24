import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SanctionsBanner } from "./SanctionsBanner";
import { SanctionsView } from "./SanctionsView";

setupMockBackend();

describe("SanctionsBanner", () => {
  it("с действующей санкцией — баннер со ссылкой на /sanctions", async () => {
    signInAsUser();
    world.scenario.social = { sanctions: ["chat"] };
    renderWithProviders(<SanctionsBanner />);
    const banner = await screen.findByTestId("sanctions-banner");
    expect(banner).toHaveTextContent("Ограничения аккаунта");
    expect(screen.getByTestId("sanctions-link")).toHaveAttribute("href", "/sanctions");
  });

  it("без санкций баннера нет", async () => {
    signInAsUser();
    renderWithProviders(<SanctionsBanner />);
    await new Promise((r) => setTimeout(r, 150));
    expect(screen.queryByTestId("sanctions-banner")).toBeNull();
  });
});

describe("SanctionsView", () => {
  it("вид, срок и причина; бессрочная — «Бессрочно»", async () => {
    signInAsUser();
    world.scenario.social = { sanctions: ["online", "account"] };
    renderWithProviders(<SanctionsView />);
    const online = await screen.findByTestId("sanction-online");
    expect(online).toHaveTextContent("Онлайн-игры");
    expect(online).toHaveTextContent("До ");
    expect(online).toHaveTextContent("Причина: Нарушение правил общения");
    expect(screen.getByTestId("sanction-account")).toHaveTextContent("Бессрочно");
  });

  it("ограничений нет — об этом сказано", async () => {
    signInAsUser();
    renderWithProviders(<SanctionsView />);
    expect(await screen.findByTestId("sanctions-empty")).toBeInTheDocument();
  });
});
