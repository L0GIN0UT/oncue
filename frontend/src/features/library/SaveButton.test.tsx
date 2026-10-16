import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { ITEMS } from "../../../e2e/mocks/world";
import { SaveButton } from "./SaveButton";

setupMockBackend();

const film = ITEMS.find((i) => i.domain === "film")!;
const song = ITEMS.find((i) => i.domain === "music")!;
const LATER = "shelf-option-system:watch_later";
const WATCHED = "shelf-option-system:watched";

describe("SaveButton и ShelfPicker", () => {
  it("гостю — ссылка на регистрацию, меню нет", async () => {
    signInAsGuest();
    renderWithProviders(<SaveButton mediaItemId={film.id} source="reveal" domain="film" />);
    await waitFor(() =>
      expect(screen.getByTestId("save-button")).toHaveAttribute("href", "/register"),
    );
  });

  it("сохранение на полку с exclusive_with сразу снимает исключающую", async () => {
    const user = userEvent.setup();
    const subject = signInAsUser();
    // Ответ сервера задерживается: отметки должны поменяться до него (оптимистично).
    server.use(
      http.put("*/api/library/v1/shelves/:ref/items/:id", async ({ params, request }) => {
        await delay(80);
        const shelf = world.ugc.shelf(subject, String(params.ref))!;
        const { source } = (await request.json()) as { source: string };
        return HttpResponse.json(world.ugc.save(subject, shelf, String(params.id), source));
      }),
    );
    renderWithProviders(<SaveButton mediaItemId={film.id} source="results" domain="film" />);
    await user.click(await screen.findByRole("button", { name: "Сохранить" }));
    await user.click(await screen.findByTestId(LATER));
    expect(screen.getByTestId(LATER)).toHaveAttribute("aria-checked", "true");
    await waitFor(() =>
      expect(world.ugc.itemShelves(subject, film.id)).toEqual(["system:watch_later"]),
    );

    await user.click(screen.getByTestId(WATCHED));
    // До ответа: «Просмотрено» отмечено, «Смотреть позже» снято (exclusive_with).
    expect(screen.getByTestId(WATCHED)).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId(LATER)).toHaveAttribute("aria-checked", "false");
    await waitFor(() =>
      expect(world.ugc.itemShelves(subject, film.id)).toEqual(["system:watched"]),
    );
    expect(screen.getByTestId(LATER)).toHaveAttribute("aria-checked", "false");
  });

  it("ошибка сохранения — откат отметки", async () => {
    const user = userEvent.setup();
    signInAsUser();
    server.use(
      http.put("*/api/library/v1/shelves/:ref/items/:id", () =>
        HttpResponse.json(
          { type: "x", title: "x", status: 409, code: "library.shelf_full" },
          { status: 409 },
        ),
      ),
    );
    renderWithProviders(<SaveButton mediaItemId={film.id} source="reveal" domain="film" />);
    await user.click(await screen.findByRole("button", { name: "Сохранить" }));
    await user.click(await screen.findByTestId(LATER));
    await waitFor(() => expect(screen.getByTestId(LATER)).toHaveAttribute("aria-checked", "false"));
    expect(await screen.findByText("Полка заполнена")).toBeInTheDocument();
  });

  it("полки по домену: у трека нет «Смотреть позже»; Escape закрывает меню", async () => {
    const user = userEvent.setup();
    signInAsUser();
    renderWithProviders(<SaveButton mediaItemId={song.id} source="reveal" domain="music" />);
    const button = await screen.findByRole("button", { name: "Сохранить" });
    await user.click(button);
    expect(await screen.findByTestId("shelf-option-system:favorite")).toBeInTheDocument();
    expect(screen.queryByTestId(LATER)).not.toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("shelf-picker")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
});
