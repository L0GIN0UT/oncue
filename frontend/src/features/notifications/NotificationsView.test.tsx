import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import type { NotificationItem } from "@/shared/api/types";
import { NotificationsView } from "./NotificationsView";

setupMockBackend();

const make = (n: number): NotificationItem => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  type: "level_up",
  title: `Заголовок ${n}`,
  body: `Текст ${n}`,
  created_at: "2026-10-24T10:00:00Z",
  read_at: null,
});

describe("NotificationsView", () => {
  it("список: новые первыми, непрочитанные помечены", async () => {
    signInAsUser();
    renderWithProviders(<NotificationsView />);
    const items = await screen.findAllByTestId("notification-item");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Новый бейдж");
    expect(items.map((i) => i.getAttribute("data-read"))).toEqual(["false", "false", "true"]);
    expect(screen.getByTestId("notifications-unread")).toHaveTextContent("Непрочитанных: 2");
  });

  it("текст уведомления выводится как текст, разметка не исполняется", async () => {
    signInAsUser();
    world.notifications.items = [
      { ...make(1), title: '<img src=x onerror="alert(1)">', body: "<script>alert(1)</script>" },
    ];
    const { container } = renderWithProviders(<NotificationsView />);
    await screen.findByTestId("notification-item");
    expect(container.querySelector("img[onerror]")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByTestId("notification-item")).toHaveTextContent("<script>alert(1)</script>");
  });

  it("«Прочитано» отмечает одно уведомление", async () => {
    signInAsUser();
    renderWithProviders(<NotificationsView />);
    const first = (await screen.findAllByTestId("notification-item"))[0]!;
    await userEvent.click(within(first).getByRole("button", { name: /Прочитано/ }));
    await waitFor(() =>
      expect(screen.getAllByTestId("notification-item")[0]).toHaveAttribute("data-read", "true"),
    );
    expect(world.notifications.unread()).toBe(1);
    expect(screen.getByTestId("notifications-unread")).toHaveTextContent("Непрочитанных: 1");
  });

  it("«Прочитать все»: всё прочитано, кнопка блокируется", async () => {
    signInAsUser();
    renderWithProviders(<NotificationsView />);
    await screen.findAllByTestId("notification-item");
    await userEvent.click(screen.getByTestId("notifications-read-all"));
    await waitFor(() =>
      expect(screen.getAllByTestId("notification-item").map((i) => i.dataset.read)).toEqual([
        "true",
        "true",
        "true",
      ]),
    );
    expect(world.notifications.unread()).toBe(0);
    expect(screen.getByTestId("notifications-read-all")).toBeDisabled();
  });

  it("курсор: «Показать ещё» догружает следующую страницу", async () => {
    signInAsUser();
    const all = Array.from({ length: 25 }, (_, i) => make(i + 1));
    server.use(
      http.get("*/api/notifications/v1/notifications", ({ request }) => {
        const start = Number(new URL(request.url).searchParams.get("cursor") ?? 0);
        const items = all.slice(start, start + 20);
        return HttpResponse.json({
          items,
          next_cursor: start + 20 < all.length ? String(start + 20) : null,
          unread: 25,
        });
      }),
    );
    renderWithProviders(<NotificationsView />);
    expect(await screen.findAllByTestId("notification-item")).toHaveLength(20);
    await userEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
    await waitFor(() => expect(screen.getAllByTestId("notification-item")).toHaveLength(25));
    expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
  });

  it("пустой список", async () => {
    signInAsUser();
    world.notifications.items = [];
    renderWithProviders(<NotificationsView />);
    expect(await screen.findByText("Уведомлений пока нет.")).toBeInTheDocument();
    expect(screen.getByTestId("notifications-read-all")).toBeDisabled();
  });

  it("ошибка загрузки — сообщение", async () => {
    signInAsUser();
    world.scenario = { notificationsDown: true };
    renderWithProviders(<NotificationsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить уведомления");
  });

  it("ошибка «прочитано» — сообщение, состояние не меняется", async () => {
    signInAsUser();
    server.use(
      http.post("*/api/notifications/v1/notifications/:id/read", () =>
        HttpResponse.json({ code: "x", title: "x", status: 500 }, { status: 500 }),
      ),
    );
    renderWithProviders(<NotificationsView />);
    const first = (await screen.findAllByTestId("notification-item"))[0]!;
    await userEvent.click(within(first).getByRole("button", { name: /Прочитано/ }));
    expect(await screen.findByTestId("notifications-action-error")).toBeInTheDocument();
    expect(screen.getAllByTestId("notification-item")[0]).toHaveAttribute("data-read", "false");
  });

  it("гость — приглашение", async () => {
    signInAsGuest();
    renderWithProviders(<NotificationsView />);
    expect(await screen.findByTestId("profile-gate")).toBeInTheDocument();
  });
});
