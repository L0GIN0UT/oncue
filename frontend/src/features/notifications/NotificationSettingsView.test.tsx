import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { NotificationSettingsView } from "./NotificationSettingsView";

setupMockBackend();

describe("NotificationSettingsView", () => {
  it("типы из данных, переключатели каналов; обязательный тип заблокирован", async () => {
    signInAsUser();
    renderWithProviders(<NotificationSettingsView />);
    expect(
      await screen.findByRole("checkbox", { name: "Новый уровень — В приложении" }),
    ).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Новый уровень — По почте" })).not.toBeChecked();
    const locked = screen.getByRole("checkbox", { name: "Решение по аватарке — В приложении" });
    expect(locked).toBeDisabled();
  });

  it("отключение канала уходит на сервер", async () => {
    signInAsUser();
    renderWithProviders(<NotificationSettingsView />);
    const box = await screen.findByRole("checkbox", { name: "Новый бейдж — В приложении" });
    await userEvent.click(box);
    await waitFor(() =>
      expect(world.notifications.settings.find((s) => s.type === "badge_unlocked")!.in_app).toBe(
        false,
      ),
    );
    expect(box).not.toBeChecked();
  });

  it("ошибка сохранения откатывает переключатель и показывает сообщение", async () => {
    signInAsUser();
    server.use(
      http.put("*/api/notifications/v1/notification-settings/:type", () =>
        HttpResponse.json({ code: "x", title: "x", status: 500 }, { status: 500 }),
      ),
    );
    renderWithProviders(<NotificationSettingsView />);
    const box = await screen.findByRole("checkbox", { name: "Новый уровень — В приложении" });
    await userEvent.click(box);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить");
    expect(box).toBeChecked();
  });

  it("согласие на рассылку: дать и отозвать", async () => {
    signInAsUser();
    renderWithProviders(<NotificationSettingsView />);
    const consent = await screen.findByRole("checkbox", {
      name: "Хочу получать информационные письма",
    });
    expect(consent).not.toBeChecked();
    await userEvent.click(consent);
    await waitFor(() => expect(world.notifications.email.subscribed).toBe(true));
    expect(screen.getByTestId("email-consent-state")).toHaveTextContent("Вы подписаны");
    await userEvent.click(consent);
    await waitFor(() => expect(world.notifications.email.subscribed).toBe(false));
    expect(screen.getByTestId("email-consent-state")).toHaveTextContent("не подписаны");
  });

  it("сервис недоступен — сообщение вместо настроек", async () => {
    signInAsUser();
    world.scenario = { notificationsDown: true };
    renderWithProviders(<NotificationSettingsView />);
    expect(await screen.findAllByRole("alert")).not.toHaveLength(0);
  });

  it("гость — приглашение", async () => {
    signInAsGuest();
    renderWithProviders(<NotificationSettingsView />);
    expect(await screen.findByTestId("profile-gate")).toBeInTheDocument();
  });
});
