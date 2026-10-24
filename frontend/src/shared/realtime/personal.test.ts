import { describe, expect, it } from "vitest";
import { parseNotificationMessage, personalChannel } from "./personal";

const valid = {
  type: "notification.created",
  notification: {
    id: "0192a000-0000-7000-8000-000000000001",
    type: "level_up",
    title: "Новый уровень",
    body: "Вы достигли 8 уровня",
    created_at: "2026-10-24T10:00:00Z",
    read_at: null,
  },
};

describe("personalChannel", () => {
  it("имя канала — personal:#<user_id> (user-limited channel)", () => {
    expect(personalChannel("abc")).toBe("personal:#abc");
  });
});

describe("parseNotificationMessage", () => {
  it("разбирает notification.created", () => {
    expect(parseNotificationMessage(valid)?.id).toBe(valid.notification.id);
  });

  it("лишние поля уведомления игнорируются", () => {
    const msg = { ...valid, notification: { ...valid.notification, extra: 1 } };
    expect(parseNotificationMessage(msg)).not.toBeNull();
  });

  it("машинные поля data сохраняются, нестроковые значения отбрасываются", () => {
    const msg = {
      ...valid,
      notification: {
        ...valid.notification,
        type: "room_invite",
        data: { room_code: "ABCD2345", n: 5, nested: { a: 1 } },
      },
    };
    expect(parseNotificationMessage(msg)?.data).toEqual({ room_code: "ABCD2345" });
  });

  it("без data поле отсутствует, битое data игнорируется", () => {
    expect(parseNotificationMessage(valid)).not.toHaveProperty("data");
    const bad = { ...valid, notification: { ...valid.notification, data: "x" } };
    expect(parseNotificationMessage(bad)).not.toHaveProperty("data");
  });

  it.each([
    null,
    "строка",
    {},
    { type: "other.event", notification: valid.notification },
    { type: "notification.created" },
    { type: "notification.created", notification: { ...valid.notification, id: 5 } },
    { type: "notification.created", notification: { ...valid.notification, title: "" } },
    { type: "notification.created", notification: { ...valid.notification, read_at: 7 } },
  ])("битое или чужое сообщение (%#) отбрасывается", (msg) => {
    expect(parseNotificationMessage(msg)).toBeNull();
  });
});
