import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { MyCollections } from "./MyCollections";

setupMockBackend();

describe("MyCollections", () => {
  it("опубликованные с плашкой «скрыт модератором», черновики — на своей вкладке", async () => {
    const user = userEvent.setup();
    const subject = signInAsUser();
    const draft = world.ugc.create(subject, "music", "Черновик игрока");
    const hidden = world.ugc.collections.find((c) => c.status === "hidden")!;
    renderWithProviders(<MyCollections />);
    const row = await screen.findByTestId(`my-collection-${hidden.id}`);
    expect(within(row).getByTestId("my-collection-hidden")).toHaveTextContent("Скрыт модератором");
    expect(screen.queryByTestId(`my-collection-${draft.id}`)).not.toBeInTheDocument();

    await user.click(screen.getByTestId("my-collections-tab-drafts"));
    const draftRow = screen.getByTestId(`my-collection-${draft.id}`);
    expect(draftRow).toHaveTextContent("Черновик игрока");
    expect(within(draftRow).queryByTestId("my-collection-hidden")).not.toBeInTheDocument();
  });

  it("удаление — с подтверждением", async () => {
    const user = userEvent.setup();
    const subject = signInAsUser();
    const draft = world.ugc.create(subject, "film", "Удалить меня");
    renderWithProviders(<MyCollections />);
    await user.click(await screen.findByTestId("my-collections-tab-drafts"));
    const row = await screen.findByTestId(`my-collection-${draft.id}`);
    await user.click(within(row).getByTestId("my-collection-delete"));
    await user.click(within(row).getByTestId("my-collection-delete-confirm"));
    await waitFor(() =>
      expect(screen.queryByTestId(`my-collection-${draft.id}`)).not.toBeInTheDocument(),
    );
    expect(draft.status).toBe("deleted");
  });

  it("гостю — предложение зарегистрироваться", async () => {
    signInAsGuest();
    renderWithProviders(<MyCollections />);
    expect(
      await screen.findByRole("heading", { name: "Сборники создают игроки с аккаунтом" }),
    ).toBeInTheDocument();
  });
});
