import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server, setupMockBackend, signInAsGuest, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { FriendsView } from "./FriendsView";

setupMockBackend();

async function open(tab: Parameters<typeof FriendsView>[0]["initialTab"] = "friends") {
  signInAsUser();
  renderWithProviders(<FriendsView initialTab={tab} />);
}

describe("FriendsView: список", () => {
  it("друзья со статусами: в сети, в матче, не в сети", async () => {
    await open();
    expect(await screen.findByTestId("friend-Анна")).toHaveAttribute("data-status", "online");
    expect(screen.getByTestId("friend-Борис")).toHaveAttribute("data-status", "in_match");
    expect(screen.getByTestId("friend-Клара")).toHaveTextContent("Не в сети");
    expect(screen.getByTestId("friend-Борис")).toHaveTextContent("В матче");
  });

  it("удаление из друзей требует подтверждения", async () => {
    await open();
    const row = await screen.findByTestId("friend-Клара");
    await userEvent.click(within(row).getByTestId("friend-remove"));
    expect(world.social.friends).toHaveLength(3);
    await userEvent.click(within(row).getByTestId("friend-remove-confirm"));
    await waitFor(() => expect(screen.queryByTestId("friend-Клара")).toBeNull());
    expect(world.social.friends).toHaveLength(2);
  });

  it("гость видит приглашение зарегистрироваться, запросов нет", async () => {
    signInAsGuest();
    renderWithProviders(<FriendsView />);
    expect(await screen.findByTestId("profile-gate")).toBeInTheDocument();
  });

  it("сбой social — понятный текст", async () => {
    server.use(
      http.get("*/api/social/v1/me/friends", () =>
        HttpResponse.json({ code: "social.unavailable", title: "x", status: 503 }, { status: 503 }),
      ),
    );
    await open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить");
  });
});

describe("FriendsView: заявки", () => {
  it("вкладка показывает число входящих; принять — друг появляется", async () => {
    await open("requests");
    const row = await screen.findByTestId("request-in-Дмитрий");
    expect(screen.getByTestId("friends-tab-requests")).toHaveTextContent("Заявки (1)");
    await userEvent.click(within(row).getByTestId("request-accept"));
    await waitFor(() => expect(screen.queryByTestId("request-in-Дмитрий")).toBeNull());
    expect(world.social.friends.some((f) => f.nickname === "Дмитрий")).toBe(true);
  });

  it("отклонить входящую и отозвать исходящую", async () => {
    world.social.outgoing = [
      { id: "out-1", user_id: "u-x", nickname: "Игорь", created_at: "2026-10-20T10:00:00Z" },
    ];
    await open("requests");
    await userEvent.click(
      within(await screen.findByTestId("request-in-Дмитрий")).getByTestId("request-decline"),
    );
    await waitFor(() => expect(world.social.incoming).toHaveLength(0));
    await userEvent.click(
      within(await screen.findByTestId("request-out-Игорь")).getByTestId("request-cancel"),
    );
    await waitFor(() => expect(world.social.outgoing).toHaveLength(0));
  });
});

describe("FriendsView: добавить и код", () => {
  it("заявка по нику уходит с Idempotency-Key; подтверждение с ником", async () => {
    const keys: (string | null)[] = [];
    server.events.on("request:start", ({ request }) => {
      if (request.method === "POST" && request.url.endsWith("/me/friend-requests"))
        keys.push(request.headers.get("idempotency-key"));
    });
    await open("add");
    await userEvent.type(await screen.findByTestId("add-nickname"), "Дмитрий");
    await userEvent.click(screen.getByTestId("add-nickname-submit"));
    expect(await screen.findByTestId("add-sent")).toHaveTextContent("Дмитрий");
    expect(keys).toHaveLength(1);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(world.social.outgoing).toHaveLength(1);
  });

  it("неизвестный ник — 404 без раскрытия причины", async () => {
    await open("add");
    await userEvent.type(await screen.findByTestId("add-nickname"), "Никто");
    await userEvent.click(screen.getByTestId("add-nickname-submit"));
    expect(await screen.findByTestId("social-error")).toHaveTextContent(
      "Игрок не найден или не принимает заявки",
    );
  });

  it("заявка по коду уходит полем code в верхнем регистре", async () => {
    let body: unknown = null;
    server.events.on("request:start", async ({ request }) => {
      if (request.method === "POST" && request.url.endsWith("/me/friend-requests"))
        body = await request.clone().json();
    });
    await open("add");
    await userEvent.type(await screen.findByTestId("add-code"), "zzzz9999");
    await userEvent.click(screen.getByTestId("add-code-submit"));
    await screen.findByTestId("add-sent");
    expect(body).toEqual({ code: "ZZZZ9999" });
  });

  it("мой код показывается и перевыпускается", async () => {
    await open("add");
    expect(await screen.findByTestId("my-code")).toHaveTextContent("ABCD2345");
    await userEvent.click(screen.getByTestId("code-regenerate"));
    await waitFor(() => expect(screen.getByTestId("my-code")).toHaveTextContent("WXYZ6789"));
  });
});

describe("FriendsView: блокировки и приватность", () => {
  it("список блокировок и разблокировка", async () => {
    world.social.blocks = [
      { user_id: "u-b", nickname: "Грубиян", created_at: "2026-10-20T10:00:00Z" },
    ];
    await open("blocks");
    const row = await screen.findByTestId("block-Грубиян");
    await userEvent.click(within(row).getByTestId("unblock"));
    await waitFor(() => expect(screen.getByTestId("blocks-empty")).toBeInTheDocument());
    expect(world.social.blocks).toHaveLength(0);
  });

  it("настройки приватности сохраняются целиком", async () => {
    await open("privacy");
    await userEvent.click(await screen.findByTestId("privacy-profile"));
    await waitFor(() => expect(world.social.privacy.profile_public).toBe(false));
    expect(world.social.privacy).toEqual({
      profile_public: false,
      stats_public: true,
      friend_requests: "all",
    });
    await userEvent.click(screen.getByTestId("privacy-requests-none"));
    await waitFor(() => expect(world.social.privacy.friend_requests).toBe("none"));
    expect(screen.getByTestId("privacy-requests-none")).toBeChecked();
  });

  it("переключатель меняется сразу, при отказе сервера возвращается", async () => {
    server.use(
      http.put("*/api/social/v1/me/privacy", () =>
        HttpResponse.json({ code: "social.unavailable", title: "x", status: 503 }, { status: 503 }),
      ),
    );
    await open("privacy");
    const box = await screen.findByTestId("privacy-profile");
    expect(box).toBeChecked();
    await userEvent.click(box);
    expect(await screen.findByTestId("social-error")).toBeInTheDocument();
    expect(screen.getByTestId("privacy-profile")).toBeChecked();
    expect(world.social.privacy.profile_public).toBe(true);
  });
});
