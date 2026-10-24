import { describe, expect, it } from "vitest";
import type { NotificationItem } from "@/shared/api/types";
import { unreadIn, withAllRead, withPrepended, withRead, type ListData } from "./cache";

const item = (id: string, read_at: string | null = null): NotificationItem => ({
  id,
  type: "level_up",
  title: id,
  body: "b",
  created_at: "2026-10-24T10:00:00Z",
  read_at,
});

const data = (): ListData => ({
  pageParams: [null, "c1"],
  pages: [
    { items: [item("a"), item("b", "2026-10-24T11:00:00Z")], next_cursor: "c1", unread: 2 },
    { items: [item("c")], next_cursor: null, unread: 2 },
  ],
});

describe("кэш списка уведомлений", () => {
  it("withRead отмечает одно и не трогает прочитанные", () => {
    const next = withRead(data(), "c", "2026-10-24T12:00:00Z");
    expect(next.pages[1]!.items[0]!.read_at).toBe("2026-10-24T12:00:00Z");
    expect(unreadIn(next)).toBe(1);
    expect(withRead(next, "b", "2030-01-01T00:00:00Z").pages[0]!.items[1]!.read_at).toBe(
      "2026-10-24T11:00:00Z",
    );
  });

  it("withAllRead отмечает все", () => {
    expect(unreadIn(withAllRead(data(), "2026-10-24T12:00:00Z"))).toBe(0);
  });

  it("withPrepended добавляет в начало и не дублирует", () => {
    const once = withPrepended(data(), item("new"));
    expect(once.pages[0]!.items[0]!.id).toBe("new");
    expect(withPrepended(once, item("new")).pages[0]!.items).toHaveLength(3);
  });

  it("исходные данные не мутируются", () => {
    const original = data();
    withAllRead(original, "x");
    expect(unreadIn(original)).toBe(2);
  });
});
