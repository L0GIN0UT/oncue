import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { SLOTS, itemByKey } from "../../../e2e/mocks/economy";
import { ShopView } from "./ShopView";

setupMockBackend();

const problem = (status: number, code: string) =>
  HttpResponse.json({ code, title: code, status }, { status });

async function open() {
  signInAsUser();
  renderWithProviders(<ShopView />);
  await screen.findByTestId("shop-balance");
}

describe("ShopView: витрина", () => {
  it("вкладки слотов — в порядке данных bff, плюс «Инвентарь» и «Оформление»", async () => {
    await open();
    const tabs = screen.getAllByRole("tab").map((t) => t.getAttribute("data-testid"));
    expect(tabs).toEqual([
      ...SLOTS.map((s) => `shop-tab-${s}`),
      "shop-tab-inventory",
      "shop-tab-appearance",
    ]);
    expect(screen.getByTestId("shop-tab-avatar")).toHaveAttribute("aria-selected", "true");
  });

  it("слот, которого нет в справочнике подписей, показывается своим ключом (без падения)", async () => {
    server.use(
      http.get("*/api/web/v1/pages/shop", () =>
        HttpResponse.json({
          blocks: {
            catalog: {
              status: "ok",
              data: {
                balance: 1,
                slots: ["season_banner"],
                items: [
                  {
                    state: "available",
                    item: {
                      ...itemByKey("fox")!,
                      slot: "season_banner",
                      key: "x1",
                      title: "Баннер",
                    },
                  },
                ],
              },
            },
            packs: null,
          },
        }),
      ),
    );
    await open();
    expect(screen.getByTestId("shop-tab-season_banner")).toHaveTextContent("season_banner");
    expect(screen.getByTestId("shop-item-x1")).toBeInTheDocument();
  });

  it("карточки: состояние, цена, уровень и редкость; закрытый предмет нельзя купить", async () => {
    await open();
    const fox = screen.getByTestId("shop-item-fox");
    expect(fox).toHaveAttribute("data-state", "available");
    expect(fox).toHaveTextContent("Лис");
    expect(fox).toHaveTextContent("20 Версов");
    expect(fox).toHaveTextContent("Обычный");
    expect(screen.getByTestId("shop-buy-fox")).toBeEnabled();

    await userEvent.click(screen.getByTestId("shop-tab-frame"));
    const neon = screen.getByTestId("shop-item-neon_frame");
    expect(neon).toHaveAttribute("data-state", "locked");
    expect(neon).toHaveTextContent("Откроется на уровне 20");
    expect(screen.queryByTestId("shop-buy-neon_frame")).toBeNull();
    expect(screen.getByTestId("shop-buy-gold_frame")).toBeInTheDocument();
  });

  it("переключение вкладок стрелками (клавиатура)", async () => {
    await open();
    screen.getByTestId("shop-tab-avatar").focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByTestId("shop-tab-frame")).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("shop-tab-frame")).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByTestId("shop-tab-appearance")).toHaveAttribute("aria-selected", "true");
  });

  it("гость — приглашение, магазин не грузится", async () => {
    signInAsGuest();
    renderWithProviders(<ShopView />);
    expect(await screen.findByTestId("profile-gate")).toBeInTheDocument();
  });

  it("блок каталога недоступен — сообщение, а не падение", async () => {
    world.scenario = { shopDown: true };
    signInAsUser();
    renderWithProviders(<ShopView />);
    expect(await screen.findByTestId("shop-unavailable")).toBeInTheDocument();
  });
});

describe("ShopView: покупка", () => {
  it("подтверждение → покупка с Idempotency-Key → баланс и состояние обновились", async () => {
    await open();
    expect(screen.getByTestId("shop-balance")).toHaveTextContent("40");
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    expect(screen.getByRole("dialog")).toHaveTextContent("Купить «Лис»?");
    await userEvent.click(screen.getByTestId("shop-confirm"));

    await waitFor(() =>
      expect(screen.getByTestId("shop-item-fox")).toHaveAttribute("data-state", "owned"),
    );
    expect(screen.getByTestId("shop-balance")).toHaveTextContent("20");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("shop-status")).toHaveTextContent("Куплено: Лис");
    expect(world.economy.lastIdempotencyKeys).toHaveLength(1);
    expect(screen.queryByTestId("shop-buy-fox")).toBeNull();
  });

  it("один ключ на один показ подтверждения: повтор после ошибки — тот же, новый показ — новый", async () => {
    world.scenario = { balance: 5 };
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не хватает Версов");
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await waitFor(() => expect(world.economy.lastIdempotencyKeys).toHaveLength(2));
    const [first, second] = world.economy.lastIdempotencyKeys;
    expect(second).toBe(first);

    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await waitFor(() => expect(world.economy.lastIdempotencyKeys).toHaveLength(3));
    expect(world.economy.lastIdempotencyKeys[2]).not.toBe(first);
  });

  it.each([
    [409, "progression.insufficient_funds", "Не хватает Версов"],
    [409, "progression.already_owned", "уже есть"],
    [403, "progression.item_not_available", "недоступен"],
    [404, "progression.item_not_found", "Такого предмета нет"],
  ])("%i %s — понятное сообщение, баланс не меняется", async (status, code, text) => {
    server.use(
      http.post("*/api/progression/v1/shop/items/:id/purchase", () => problem(status, code)),
    );
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-owl"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByTestId("shop-balance")).toHaveTextContent("40");
  });

  it("already_owned помечает предмет купленным", async () => {
    server.use(
      http.post("*/api/progression/v1/shop/items/:id/purchase", () =>
        problem(409, "progression.already_owned"),
      ),
    );
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByTestId("shop-item-fox")).toHaveAttribute("data-state", "owned");
  });

  it("429 и 5xx — отдельные сообщения (с Retry-After), без «не хватает Версов»", async () => {
    server.use(
      http.post("*/api/progression/v1/shop/items/:id/purchase", () =>
        HttpResponse.json(
          { code: "rate_limited", title: "x", status: 429 },
          { status: 429, headers: { "Retry-After": "12" } },
        ),
      ),
    );
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Повторите через 12");

    server.use(http.post("*/api/progression/v1/shop/items/:id/purchase", () => problem(503, "x")));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("временно недоступна"));
  });

  it("после «не хватает Версов» витрина перечитывается (баланс актуален)", async () => {
    await open();
    let pageLoads = 0;
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("/pages/shop")) pageLoads += 1;
    });
    server.use(
      http.post("*/api/progression/v1/shop/items/:id/purchase", () =>
        problem(409, "progression.insufficient_funds"),
      ),
    );
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await screen.findByRole("alert");
    await waitFor(() => expect(pageLoads).toBeGreaterThanOrEqual(1));
  });

  it("пока идёт покупка, остальные «Купить» заблокированы и не открывают диалог", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    server.use(
      http.post("*/api/progression/v1/shop/items/:id/purchase", async () => {
        await gate;
        return problem(409, "progression.insufficient_funds");
      }),
    );
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.click(screen.getByTestId("shop-confirm"));
    await waitFor(() => expect(screen.getByTestId("shop-buy-owl")).toBeDisabled());
    expect(screen.getByTestId("shop-buy-fox")).toBeDisabled();
    release();
    await screen.findByRole("alert");
    expect(screen.getByTestId("shop-buy-owl")).toBeEnabled();
  });

  it("диалог модальный: aria-modal и фокус не уходит за его пределы по Tab", async () => {
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByTestId("shop-confirm")).toHaveFocus();
    await userEvent.tab();
    expect(within(dialog).getByRole("button", { name: "Отмена" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByTestId("shop-confirm")).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(within(dialog).getByRole("button", { name: "Отмена" })).toHaveFocus();
  });

  it("Escape закрывает подтверждение без покупки", async () => {
    await open();
    await userEvent.click(screen.getByTestId("shop-buy-fox"));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(world.economy.lastIdempotencyKeys).toHaveLength(0);
  });
});

describe("ShopView: инвентарь и оформление", () => {
  it("инвентарь: надеть и снять предмет", async () => {
    world.economy.owned.set(itemByKey("fox")!.id, "purchase");
    await open();
    await userEvent.click(screen.getByTestId("shop-tab-inventory"));
    const entry = await screen.findByTestId("inventory-item-fox");
    expect(entry).toHaveTextContent("Лис");
    await userEvent.click(within(entry).getByRole("button", { name: "Надеть" }));
    await waitFor(() =>
      expect(screen.getByTestId("inventory-item-fox")).toHaveAttribute("data-equipped", "true"),
    );
    expect(world.economy.equipped.get("avatar")).toBe(itemByKey("fox")!.id);

    await userEvent.click(
      within(screen.getByTestId("inventory-item-fox")).getByRole("button", { name: "Снять" }),
    );
    await waitFor(() => expect(world.economy.equipped.has("avatar")).toBe(false));
  });

  it("оформление: выбор предмета слота и пустой слот", async () => {
    world.economy.owned.set(itemByKey("fox")!.id, "purchase");
    world.economy.owned.set(itemByKey("champion")!.id, "promo");
    await open();
    await userEvent.click(screen.getByTestId("shop-tab-appearance"));
    const select = await screen.findByTestId("equip-avatar");
    await userEvent.selectOptions(select, itemByKey("fox")!.id);
    await waitFor(() => expect(world.economy.equipped.get("avatar")).toBe(itemByKey("fox")!.id));
    await userEvent.selectOptions(screen.getByTestId("equip-avatar"), "");
    await waitFor(() => expect(world.economy.equipped.has("avatar")).toBe(false));
    // В слоте «рамка» нет своих предметов — выбирать нечего, кроме «Пусто».
    expect(within(screen.getByTestId("equip-frame")).getAllByRole("option")).toHaveLength(1);
  });

  it("ошибка оформления показывается сообщением", async () => {
    world.economy.owned.set(itemByKey("fox")!.id, "purchase");
    server.use(
      http.put("*/api/progression/v1/me/appearance/:slot", () =>
        problem(404, "progression.item_not_owned"),
      ),
    );
    await open();
    await userEvent.click(screen.getByTestId("shop-tab-appearance"));
    await userEvent.selectOptions(await screen.findByTestId("equip-avatar"), itemByKey("fox")!.id);
    expect(await screen.findByRole("alert")).toHaveTextContent("нет в вашем инвентаре");
  });

  it("блок инвентаря недоступен — сообщение", async () => {
    world.scenario = { appearanceDown: true };
    await open();
    await userEvent.click(screen.getByTestId("shop-tab-inventory"));
    expect(await screen.findByTestId("inventory-unavailable")).toBeInTheDocument();
  });
});
