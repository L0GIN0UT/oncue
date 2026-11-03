import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { NotificationsPanel } from "./NotificationsPanel";

setupMockBackend();

const FROM = "00000000-0000-4000-8000-000000000902";

describe("панель уведомлений: заявка в друзья", () => {
  it("строка ведёт в профиль отправителя, помечается прочитанной и закрывает панель", async () => {
    signInAsUser();
    world.notifications.items = [
      {
        id: "00000000-0000-4000-8000-000000000101",
        type: "friend_request",
        title: "Заявка в друзья",
        body: "Анна хочет добавить вас в друзья.",
        created_at: "2026-10-24T10:00:00Z",
        read_at: null,
        data: { user_id: FROM },
      },
    ];
    const onNavigate = vi.fn();
    renderWithProviders(<NotificationsPanel id="p" onNavigate={onNavigate} />);
    const link = await screen.findByTestId("notification-friend-link");
    expect(link).toHaveAttribute("href", `/players/${FROM}`);
    await userEvent.click(link);
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });
});
