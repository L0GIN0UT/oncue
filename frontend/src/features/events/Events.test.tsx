import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { HeaderEventLink } from "./EventBanner";
import { EventSpot } from "./EventSpot";
import { EventView } from "./EventView";
import { EventsArchive } from "./EventsArchive";

setupMockBackend();

describe("EventView", () => {
  it("карта, дневник и прогресс: найденная, открытая, закрытая", async () => {
    signInAsUser();
    world.scenario.event = "active";
    renderWithProviders(<EventView eventKey="winter" />);

    expect(await screen.findByTestId("event-progress")).toHaveTextContent("Найдено 1 из 4");
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "1");
    expect(screen.getByTestId("event-point-lantern")).toHaveAttribute("data-state", "found");
    expect(screen.getByTestId("event-point-snowman")).toHaveAttribute("data-state", "open");
    // Закрытая находка не кликабельна и не раскрывает подсказку в дневнике.
    expect(screen.getByTestId("event-point-star")).toBeDisabled();
    expect(screen.getByTestId("event-diary-star")).toHaveTextContent("Пока скрыто");
    expect(screen.getByTestId("event-diary-lantern")).toHaveTextContent("тайнику с вареньем");
    expect(screen.getByTestId("event-reward")).toHaveAttribute("data-completed", "false");
  });

  it("точка открывает карточку находки с кнопкой «играть» и прогрессом", async () => {
    signInAsUser();
    world.scenario.event = "active";
    renderWithProviders(<EventView eventKey="winter" />);

    fireEvent.click(await screen.findByTestId("event-point-duelist"));
    expect(screen.getByTestId("event-find-progress")).toHaveTextContent("1 из 3");
    expect(screen.getByTestId("event-play")).toHaveAttribute("href", "/online");
  });

  it("неизвестное событие — 404 по-человечески", async () => {
    signInAsUser();
    renderWithProviders(<EventView eventKey="ghost" />);
    expect(await screen.findByTestId("event-error")).toHaveTextContent("Событие не найдено");
  });

  it("гость — предложение завести аккаунт, запросов нет", async () => {
    signInAsGuest();
    renderWithProviders(<EventView eventKey="winter" />);
    expect(await screen.findByTestId("profile-gate")).toBeInTheDocument();
  });
});

describe("EventSpot", () => {
  it("рисуется только на странице открытой находки; клик засчитывает и обновляет данные", async () => {
    signInAsUser();
    world.scenario.event = "active";
    renderWithProviders(<EventSpot pageKey="shop" />);

    const spot = await screen.findByTestId("event-spot");
    expect(spot).toHaveAttribute("aria-label", "Что-то блестит — присмотреться");
    fireEvent.click(spot);

    expect(await screen.findByText("Находка открыта!")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("event-spot")).toBeNull());
    expect(world.events.claimCalls).toBe(1);
    expect(world.events.found.has("snowman")).toBe(true);
  });

  it("чужая страница, найденная находка, нет события, аноним — пусто", async () => {
    signInAsUser();
    world.scenario.event = "active";
    const { unmount } = renderWithProviders(<EventSpot pageKey="leaderboards" />);
    // Дождёмся загрузки данных события: на «чужой» странице элемента всё равно нет.
    await new Promise((r) => setTimeout(r, 80));
    expect(screen.queryByTestId("event-spot")).toBeNull();
    unmount();

    renderWithProviders(<EventSpot pageKey="home" />); // находка «lantern» уже найдена
    await new Promise((r) => setTimeout(r, 80));
    expect(screen.queryByTestId("event-spot")).toBeNull();
  });

  it("события нет — элемента нет и засчёт не вызывается", async () => {
    signInAsUser();
    renderWithProviders(<EventSpot pageKey="shop" />);
    await new Promise((r) => setTimeout(r, 80));
    expect(screen.queryByTestId("event-spot")).toBeNull();
    expect(world.events.claimCalls).toBe(0);
  });

  it("событие закончилось между загрузкой и кликом — тост об ошибке, элемент остаётся честным", async () => {
    signInAsUser();
    world.scenario.event = "active";
    world.scenario.eventClaimConflict = true;
    renderWithProviders(<EventSpot pageKey="shop" />);

    fireEvent.click(await screen.findByTestId("event-spot"));
    expect(await screen.findByText("Не удалось засчитать находку.")).toBeInTheDocument();
  });
});

describe("баннер и архив", () => {
  it("ссылка в шапке ведёт на событие и показывает прогресс игрока", async () => {
    signInAsUser();
    world.scenario.event = "active";
    renderWithProviders(<HeaderEventLink />);
    const link = await screen.findByTestId("nav-event");
    expect(link).toHaveAttribute("href", "/events/winter");
    await waitFor(() => expect(link).toHaveTextContent("Найдено 1 из 4"));
  });

  it("аноним видит баннер без прогресса; без события ссылки нет", async () => {
    world.scenario.event = "active";
    const { unmount } = renderWithProviders(<HeaderEventLink />);
    const link = await screen.findByTestId("nav-event");
    expect(link).not.toHaveTextContent("Найдено");
    unmount();

    world.scenario.event = undefined;
    renderWithProviders(<HeaderEventLink />);
    await new Promise((r) => setTimeout(r, 80));
    expect(screen.queryByTestId("nav-event")).toBeNull();
  });

  it("архив: записи со ссылками; пустой архив ничего не рисует", () => {
    const { unmount } = renderWithProviders(<EventsArchive events={[]} />);
    expect(screen.queryByTestId("events-archive")).toBeNull();
    unmount();
    renderWithProviders(
      <EventsArchive
        events={[
          {
            key: "autumn",
            title: "Осенний переполох",
            status: "finished",
            starts_at: "2025-10-01T00:00:00Z",
            ends_at: "2025-10-31T00:00:00Z",
            found: 5,
            total: 5,
            completed: true,
          },
        ]}
      />,
    );
    expect(screen.getByRole("link", { name: "Осенний переполох" })).toHaveAttribute(
      "href",
      "/events/autumn",
    );
    expect(screen.getByTestId("events-archive-autumn")).toHaveTextContent("Пройдено");
  });
});
