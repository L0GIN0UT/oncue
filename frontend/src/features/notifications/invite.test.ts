import { describe, expect, it } from "vitest";
import type { NotificationItem } from "@/shared/api/types";
import { roomInviteHref } from "./invite";

const base: NotificationItem = {
  id: "1",
  type: "room_invite",
  title: "Приглашение",
  body: "Анна зовёт вас играть",
  created_at: "2026-10-24T10:00:00Z",
  read_at: null,
  data: { room_code: "ABCD2345" },
};

describe("roomInviteHref", () => {
  it("room_invite с кодом ведёт в комнату", () => {
    expect(roomInviteHref(base)).toBe("/room/ABCD2345");
  });

  it("код нормализуется до сравнения с форматом комнат", () => {
    expect(roomInviteHref({ ...base, data: { room_code: "abcd-2345" } })).toBe("/room/ABCD2345");
  });

  it.each([
    { ...base, type: "level_up" },
    { ...base, data: undefined },
    { ...base, data: {} },
    { ...base, data: { room_code: "../../evil" } },
    { ...base, data: { room_code: "ABC" } },
  ])("прочее — без ссылки (%#)", (item) => {
    expect(roomInviteHref(item as NotificationItem)).toBeNull();
  });
});
