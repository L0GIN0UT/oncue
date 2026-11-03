import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupMockBackend, signInAsUser, world } from "@/test/msw";
import { renderWithProviders } from "@/test/render";
import { PLAYERS } from "../../../e2e/mocks/progression";
import { NotificationsView } from "@/features/notifications/NotificationsView";
import { PlayerView } from "@/features/progression/PlayerView";

setupMockBackend();

const PLAYER = PLAYERS[0]!;

describe("профиль игрока: social", () => {
  it("не друг: «Добавить в друзья» шлёт заявку по user_id; блокировка с подтверждением", async () => {
    signInAsUser();
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    await userEvent.click(await screen.findByTestId("player-add-friend"));
    expect(await screen.findByTestId("player-add-friend-sent")).toBeInTheDocument();
    expect(world.social.outgoing.map((r) => r.user_id)).toEqual([PLAYER.user_id]);
    await userEvent.click(screen.getByTestId("player-block"));
    expect(world.social.blocks).toHaveLength(0);
    await userEvent.click(screen.getByTestId("player-block-confirm"));
    await waitFor(() => expect(world.social.blocks).toHaveLength(1));
  });

  it("друг: метка и удаление", async () => {
    signInAsUser();
    world.social.friends.push({
      user_id: PLAYER.user_id,
      nickname: "Игрок",
      status: "offline",
      friends_since: "2026-10-01T10:00:00Z",
    });
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-is-friend")).toBeInTheDocument();
    expect(screen.queryByTestId("player-add-friend")).toBeNull();
    await userEvent.click(screen.getByTestId("player-remove-friend"));
    await waitFor(() => expect(world.social.isFriend(PLAYER.user_id)).toBe(false));
  });

  it("restricted: только оформление и пояснение, без уровня и бейджей", async () => {
    signInAsUser();
    world.scenario.social = { restrictedPlayer: PLAYER.user_id };
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    const box = await screen.findByTestId("player-restricted");
    expect(box).toHaveTextContent("Профиль недоступен");
    expect(screen.queryByTestId("player-level")).toBeNull();
    expect(screen.queryByTestId("player-badges")).toBeNull();
    expect(screen.queryByTestId("player-block")).toBeNull();
  });

  it("заблокированный мной игрок: пояснение и «Разблокировать»", async () => {
    signInAsUser();
    world.social.blocks = [
      { user_id: PLAYER.user_id, nickname: "Игрок", created_at: "2026-10-20T10:00:00Z" },
    ];
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    await userEvent.click(await screen.findByTestId("player-unblock"));
    await waitFor(() => expect(world.social.blocks).toHaveLength(0));
  });
});

describe("уведомление room_invite", () => {
  it("кнопка «Войти» ведёт в комнату по data.room_code", async () => {
    signInAsUser();
    world.notifications.items = [
      {
        id: "00000000-0000-4000-8000-000000000001",
        type: "room_invite",
        title: "Приглашение в игру",
        body: "Анна зовёт вас в комнату",
        created_at: "2026-10-24T10:00:00Z",
        read_at: null,
        data: { room_code: "ABCD2345" },
      },
      {
        id: "00000000-0000-4000-8000-000000000002",
        type: "level_up",
        title: "Уровень",
        body: "Новый",
        created_at: "2026-10-24T09:00:00Z",
        read_at: null,
      },
    ];
    renderWithProviders(<NotificationsView />);
    const join = await screen.findByTestId("notification-join");
    expect(join).toHaveAttribute("href", "/room/ABCD2345");
    expect(screen.getAllByTestId("notification-join")).toHaveLength(1);
  });
});

describe("входящая заявка от игрока на его профиле", () => {
  const request = () => ({
    id: "0192b000-0000-7000-8001-0000000000aa",
    user_id: PLAYER.user_id,
    nickname: "Игрок",
    created_at: "2026-10-01T10:00:00Z",
  });

  it("вместо «Добавить» — «Принять» и «Отклонить»; принять делает друзьями", async () => {
    signInAsUser();
    world.social.incoming = [request()];
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-requested-you")).toBeInTheDocument();
    expect(screen.queryByTestId("player-add-friend")).toBeNull();
    await userEvent.click(screen.getByTestId("player-accept-request"));
    await waitFor(() => expect(world.social.isFriend(PLAYER.user_id)).toBe(true));
    expect(world.social.incoming).toHaveLength(0);
    expect(await screen.findByTestId("player-is-friend")).toBeInTheDocument();
  });

  it("отклонить убирает заявку, снова доступно «Добавить в друзья»", async () => {
    signInAsUser();
    world.social.incoming = [request()];
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    await userEvent.click(await screen.findByTestId("player-decline-request"));
    await waitFor(() => expect(world.social.incoming).toHaveLength(0));
    expect(world.social.isFriend(PLAYER.user_id)).toBe(false);
    expect(await screen.findByTestId("player-add-friend")).toBeInTheDocument();
    expect(screen.queryByTestId("player-accept-request")).toBeNull();
  });

  it("заявка от другого игрока на этом профиле не мешает: «Добавить» на месте", async () => {
    signInAsUser();
    renderWithProviders(<PlayerView userId={PLAYER.user_id} />);
    expect(await screen.findByTestId("player-add-friend")).toBeInTheDocument();
    expect(screen.queryByTestId("player-accept-request")).toBeNull();
  });
});

describe("уведомления о друзьях", () => {
  const notification = (id: string, type: string, data?: Record<string, string>) => ({
    id: `00000000-0000-4000-8000-0000000001${id}`,
    type,
    title: type === "friend_request" ? "Заявка в друзья" : "Заявка принята",
    body: "Анна",
    created_at: "2026-10-24T10:00:00Z",
    read_at: null,
    ...(data ? { data } : {}),
  });

  it("заявка с data.user_id ведёт в профиль игрока", async () => {
    signInAsUser();
    world.notifications.items = [notification("01", "friend_request", { user_id: PLAYER.user_id })];
    renderWithProviders(<NotificationsView />);
    expect(await screen.findByTestId("notification-friend-link")).toHaveAttribute(
      "href",
      `/players/${PLAYER.user_id}`,
    );
  });

  it("сервер ещё не кладёт user_id — заявка ведёт на вкладку заявок, принятие — к друзьям", async () => {
    signInAsUser();
    world.notifications.items = [
      notification("01", "friend_request"),
      notification("02", "friend_accepted"),
    ];
    renderWithProviders(<NotificationsView />);
    await screen.findAllByTestId("notification-friend-link");
    const hrefs = screen
      .getAllByTestId("notification-friend-link")
      .map((l) => l.getAttribute("href"));
    expect(hrefs).toEqual(["/friends?tab=requests", "/friends"]);
  });

  it("мусор в data.user_id в адрес не попадает", async () => {
    signInAsUser();
    world.notifications.items = [notification("01", "friend_request", { user_id: "../../admin" })];
    renderWithProviders(<NotificationsView />);
    expect(await screen.findByTestId("notification-friend-link")).toHaveAttribute(
      "href",
      "/friends?tab=requests",
    );
  });
});
