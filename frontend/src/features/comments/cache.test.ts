import { describe, expect, it } from "vitest";
import type { CommentView } from "@/shared/api/types";
import { removeComment, replaceComment, prependComment, type CommentsData } from "./cache";

const c = (id: string, body = id): CommentView => ({
  id,
  collection_id: "col",
  author_id: "a",
  author_nickname: "Анна",
  body,
  created_at: "2026-10-24T10:00:00Z",
  edited_at: null,
  can_edit: false,
  can_delete: false,
});

const data = (): CommentsData => ({
  pageParams: [null, "2"],
  pages: [
    { items: [c("1"), c("2")], next_cursor: "2", total: 3 },
    { items: [c("3")], next_cursor: null, total: 3 },
  ],
});

describe("кэш комментариев", () => {
  it("новый комментарий — в начало первой страницы, total растёт", () => {
    const next = prependComment(data(), c("0"));
    expect(next.pages[0]!.items.map((x) => x.id)).toEqual(["0", "1", "2"]);
    expect(next.pages.every((p) => p.total === 4)).toBe(true);
  });

  it("правка заменяет комментарий на любой странице", () => {
    const next = replaceComment(data(), c("3", "новый текст"));
    expect(next.pages[1]!.items[0]!.body).toBe("новый текст");
  });

  it("удаление убирает комментарий и уменьшает total", () => {
    const next = removeComment(data(), "2");
    expect(next.pages[0]!.items.map((x) => x.id)).toEqual(["1"]);
    expect(next.pages.every((p) => p.total === 2)).toBe(true);
  });

  it("удаление несуществующего ничего не меняет", () => {
    expect(removeComment(data(), "zzz")).toEqual(data());
  });
});
