import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { setupMockBackend, signInAsUser } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { BannedView } from "./BannedView";

setupMockBackend();

afterEach(() => vi.unstubAllGlobals());

describe("BannedView", () => {
  it("«Аккаунт заблокирован» без причины и срока, с кнопкой выхода", () => {
    renderWithProviders(<BannedView />);
    expect(screen.getByRole("heading", { name: "Аккаунт заблокирован" })).toBeInTheDocument();
    expect(screen.queryByText(/причин/i)).toBeNull();
    expect(screen.getByTestId("banned-logout")).toHaveTextContent("Выйти");
  });

  it("выход завершает сессию на сервере и ведёт на главную", async () => {
    signInAsUser();
    const assign = vi.fn();
    vi.stubGlobal("location", {
      ...globalThis.location,
      assign,
      origin: globalThis.location.origin,
    });
    renderWithProviders(<BannedView />);
    fireEvent.click(screen.getByTestId("banned-logout"));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("/"));
    expect(document.cookie).not.toContain("gv_access=user");
  });
});
