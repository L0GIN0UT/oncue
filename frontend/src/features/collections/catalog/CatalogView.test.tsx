import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsUser } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { CatalogView } from "./CatalogView";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/collections",
  useSearchParams: () => new URLSearchParams("domain=film&sort=new"),
}));

setupMockBackend();

describe("CatalogView: поиск", () => {
  beforeEach(() => {
    replace.mockClear();
    signInAsUser();
  });
  afterEach(() => vi.restoreAllMocks());

  it("ввод пишет q в адрес через history.replaceState, а не router.replace", async () => {
    const spy = vi.spyOn(window.history, "replaceState");
    renderWithProviders(<CatalogView />);
    await userEvent.type(await screen.findByTestId("collection-search"), "кадры");
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const url = String(spy.mock.calls.at(-1)?.[2]);
    expect(url).toContain("/collections?");
    const query = new URLSearchParams(url.split("?")[1]);
    expect(Object.fromEntries(query)).toEqual({ domain: "film", sort: "new", q: "кадры" });
    // router.replace отменял бы router.push карточки, на которую кликнули сразу после ввода.
    expect(replace).not.toHaveBeenCalled();
  });
});
