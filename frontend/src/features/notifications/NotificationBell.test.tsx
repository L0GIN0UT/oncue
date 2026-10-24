import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { NotificationItem } from "@/shared/api/types";
import type { PersonalHandlers, PersonalTransportFactory } from "@/shared/realtime/personal";
import { NotificationBell } from "./NotificationBell";

setupMockBackend();

type Channel = { userId: string; handlers: PersonalHandlers; stopped: boolean };

const ME = "00000000-0000-4000-8000-000000000901";

/** В unit-моках нет identity `/v1/me` (его даёт мок онлайна) — нужен для user_id канала. */
function installChannel() {
  server.use(http.get("*/api/identity/v1/me", () => HttpResponse.json({ id: ME })));
  const channels: Channel[] = [];
  const factory: PersonalTransportFactory = (userId) => {
    const ch: Channel = { userId, handlers: null as never, stopped: false };
    channels.push(ch);
    return {
      start(h) {
        ch.handlers = h;
      },
      stop() {
        ch.stopped = true;
      },
    };
  };
  (globalThis as { __GV_E2E_PERSONAL__?: PersonalTransportFactory }).__GV_E2E_PERSONAL__ = factory;
  return channels;
}

const created = (id = "n-100"): NotificationItem => ({
  id,
  type: "quest_completed",
  title: "Задание",
  body: "Выполнено",
  created_at: "2026-10-24T10:00:00Z",
  read_at: null,
});

afterEach(() => {
  delete (globalThis as { __GV_E2E_PERSONAL__?: unknown }).__GV_E2E_PERSONAL__;
  vi.useRealTimers();
});

describe("NotificationBell", () => {
  it("показывает число непрочитанных и подписывается на personal:#<user_id>", async () => {
    const channels = installChannel();
    signInAsUser();
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByTestId("notification-count")).toHaveTextContent("2");
    // Число озвучивается одним способом — живой областью; имя ссылки статично.
    expect(screen.getByTestId("notification-bell")).toHaveAttribute("aria-label", "Уведомления");
    expect(screen.getByTestId("notification-live")).toHaveTextContent("Непрочитанных: 2");
    await waitFor(() => expect(channels).toHaveLength(1));
    expect(channels[0]!.userId).toBe(ME);
  });

  it("событие notification.created освежает счётчик с сервера; дубль доставки не считается", async () => {
    const channels = installChannel();
    signInAsUser();
    let reads = 0;
    server.events.on("request:start", ({ request }) => {
      if (request.url.includes("unread-count")) reads += 1;
    });
    renderWithProviders(<NotificationBell />);
    await screen.findByTestId("notification-count");
    await waitFor(() => expect(channels[0]?.handlers).toBeTruthy());
    await waitFor(() => expect(reads).toBeGreaterThanOrEqual(1));
    const count = () => screen.getByTestId("notification-count");

    world.notifications.push();
    const before = reads;
    act(() => channels[0]!.handlers.onNotification(created("n-100")));
    await waitFor(() => expect(count()).toHaveTextContent("3"));
    // Дубль доставки не вызывает лишнего запроса.
    const afterFirst = reads;
    expect(afterFirst).toBeGreaterThan(before);
    act(() => channels[0]!.handlers.onNotification(created("n-100")));
    await new Promise((r) => setTimeout(r, 50));
    expect(reads).toBe(afterFirst);

    world.notifications.push();
    act(() => channels[0]!.handlers.onNotification(created("n-101")));
    await waitFor(() => expect(count()).toHaveTextContent("4"));
  });

  it("событие без подтверждения сервером не накручивает счётчик вслепую", async () => {
    const channels = installChannel();
    signInAsUser();
    renderWithProviders(<NotificationBell />);
    await screen.findByTestId("notification-count");
    await waitFor(() => expect(channels[0]?.handlers).toBeTruthy());
    act(() => channels[0]!.handlers.onNotification(created("ghost-1")));
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.getByTestId("notification-count")).toHaveTextContent("2");
  });

  it("при размонтировании канал закрывается", async () => {
    const channels = installChannel();
    signInAsUser();
    const view = renderWithProviders(<NotificationBell />);
    await waitFor(() => expect(channels).toHaveLength(1));
    view.unmount();
    expect(channels[0]!.stopped).toBe(true);
  });

  it("без непрочитанных числа нет", async () => {
    installChannel();
    signInAsUser();
    for (const i of world.notifications.items) i.read_at = "2026-10-24T00:00:00Z";
    renderWithProviders(<NotificationBell />);
    await waitFor(() => expect(screen.getByTestId("notification-live")).toBeEmptyDOMElement());
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("notification-count")).toBeNull();
  });

  it("больше 99 — «99+»", async () => {
    installChannel();
    signInAsUser();
    server.use(
      http.get("*/api/notifications/v1/notifications/unread-count", () =>
        HttpResponse.json({ count: 250 }),
      ),
    );
    renderWithProviders(<NotificationBell />);
    await waitFor(() => expect(screen.getByTestId("notification-count")).toHaveTextContent("99+"));
  });

  it("страховочный опрос раз в 60 с; в скрытой вкладке не опрашивает", async () => {
    installChannel();
    signInAsUser();
    let calls = 0;
    server.use(
      http.get("*/api/notifications/v1/notifications/unread-count", () => {
        calls += 1;
        return HttpResponse.json({ count: calls });
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderWithProviders(<NotificationBell />);
    await waitFor(() => expect(calls).toBe(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    await waitFor(() => expect(calls).toBe(2));

    // Вкладка скрыта: опрос стоит.
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    expect(calls).toBe(2);

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    await waitFor(() => expect(calls).toBeGreaterThanOrEqual(3));
  });

  it("сбой shell и счётчика не ломает шапку: колокольчик без числа", async () => {
    installChannel();
    signInAsUser();
    world.scenario = { shellDown: true, notificationsDown: true };
    renderWithProviders(<NotificationBell />);
    expect(await screen.findByTestId("notification-bell")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("notification-count")).toBeNull();
  });

  it("гостю канал не открывается", async () => {
    const channels = installChannel();
    signInAsGuest();
    renderWithProviders(<NotificationBell />);
    await new Promise((r) => setTimeout(r, 50));
    expect(channels).toHaveLength(0);
    expect(screen.queryByTestId("notification-count")).toBeNull();
  });
});
