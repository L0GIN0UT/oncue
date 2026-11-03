import { describe, expect, it } from "vitest";
import { friendNotificationLink } from "./friend";

const ID = "0192b000-0000-7000-8001-0000000000AA";

describe("friendNotificationLink", () => {
  it("friend_request и friend_accepted с user_id — профиль игрока", () => {
    for (const type of ["friend_request", "friend_accepted"]) {
      expect(friendNotificationLink({ type, data: { user_id: ID } })).toEqual({
        href: `/players/${ID.toLowerCase()}`,
        labelKey: "notifications.openProfile",
      });
    }
  });

  it("без user_id: заявка — на вкладку заявок, принятие — к друзьям", () => {
    expect(friendNotificationLink({ type: "friend_request", data: undefined })?.href).toBe(
      "/friends?tab=requests",
    );
    expect(friendNotificationLink({ type: "friend_accepted", data: {} })?.href).toBe("/friends");
  });

  it("user_id не UUID — в адрес не попадает", () => {
    expect(
      friendNotificationLink({ type: "friend_request", data: { user_id: "x/../y" } })?.href,
    ).toBe("/friends?tab=requests");
    expect(
      friendNotificationLink({ type: "friend_request", data: { user_id: 42 as unknown as string } })
        ?.href,
    ).toBe("/friends?tab=requests");
  });

  it("другие типы — не наши", () => {
    expect(friendNotificationLink({ type: "level_up", data: { user_id: ID } })).toBeNull();
    expect(friendNotificationLink({ type: "room_invite", data: undefined })).toBeNull();
  });
});
