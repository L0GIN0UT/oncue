/** Чистые обновления кэша постраничных комментариев (TanStack `InfiniteData`). */
import type { CommentCard as CommentView, CommentsCard as CommentPage } from "@/shared/api/types";

export type CommentsData = { pages: CommentPage[]; pageParams: (string | null)[] };

const withTotal = (pages: CommentPage[], delta: number): CommentPage[] =>
  pages.map((p) => ({ ...p, total: Math.max(0, p.total + delta) }));

export function prependComment(data: CommentsData, comment: CommentView): CommentsData {
  const [first, ...rest] = data.pages;
  if (!first) return data;
  return {
    ...data,
    pages: withTotal([{ ...first, items: [comment, ...first.items] }, ...rest], 1),
  };
}

export function replaceComment(data: CommentsData, comment: CommentView): CommentsData {
  return {
    ...data,
    pages: data.pages.map((p) => ({
      ...p,
      items: p.items.map((x) => (x.id === comment.id ? comment : x)),
    })),
  };
}

export function removeComment(data: CommentsData, id: string): CommentsData {
  if (!data.pages.some((p) => p.items.some((x) => x.id === id))) return data;
  return {
    ...data,
    pages: withTotal(
      data.pages.map((p) => ({ ...p, items: p.items.filter((x) => x.id !== id) })),
      -1,
    ),
  };
}
