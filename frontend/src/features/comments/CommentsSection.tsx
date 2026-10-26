"use client";
/**
 * Комментарии на странице сборника (этап 5c). Первая страница приходит блоком bff
 * `blocks.comments`, дальше — «показать ещё» по `next_cursor` (social
 * `GET /v1/collections/{id}/comments`). Писать, править (пока сервер даёт `can_edit` — 5 минут),
 * удалять (`can_delete`: автор, автор сборника, модератор) и жаловаться может пользователь;
 * гостю — приглашение войти. Тексты комментариев — только как текст, React экранирует.
 */
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import type { CommentCard as CommentView, CommentsCard } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useMyUserId } from "@/features/auth/use-my-user-id";
import { useViewer } from "@/features/auth/session";
import { ErrorLine } from "@/features/friends/ErrorLine";
import { COMMENT_MAX_LENGTH } from "@/features/friends/config";
import { useSocialAction } from "@/features/friends/use-action";
import { deleteComment, editComment, fetchComments, postComment } from "@/features/friends/api";
import { ReportButton } from "@/features/reports/ReportDialog";
import { prependComment, removeComment, replaceComment, type CommentsData } from "./cache";

export const commentsKey = (collectionId: string) => ["social", "comments", collectionId] as const;

type Props = {
  collectionId: string;
  /** Блок bff: `null` — не пришёл (секцию не рисуем), `unavailable` — «временно недоступно». */
  block: { status: "ok" | "unavailable"; data?: CommentsCard | null } | null | undefined;
};

export function CommentsSection({ collectionId, block }: Props) {
  const t = useTranslations();
  if (!block) return null;
  return (
    <section
      aria-labelledby="comments-title"
      className="flex flex-col gap-4"
      data-testid="comments"
    >
      <h2 id="comments-title" className="font-semibold">
        {t("social.comments.title")}
      </h2>
      {block.status !== "ok" || !block.data ? (
        <p className="text-ink-muted" data-testid="comments-unavailable">
          {t("social.comments.unavailable")}
        </p>
      ) : (
        <CommentsBody collectionId={collectionId} first={block.data} />
      )}
    </section>
  );
}

function CommentsBody({ collectionId, first }: { collectionId: string; first: CommentsCard }) {
  const t = useTranslations();
  const viewer = useViewer();
  const myId = useMyUserId();
  const client = useQueryClient();
  const key = commentsKey(collectionId);

  const query = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => fetchComments(collectionId, pageParam),
    initialPageParam: null as string | null,
    initialData: { pages: [first], pageParams: [null] },
    getNextPageParam: (last) => last.next_cursor,
    staleTime: 30_000,
  });
  const pages = query.data.pages;
  const items = pages.flatMap((p) => p.items);
  const total = pages[0]?.total ?? items.length;

  const patch = (fn: (d: CommentsData) => CommentsData) =>
    client.setQueryData<CommentsData>(key, (d) => (d ? fn(d) : d));

  return (
    <>
      {viewer.kind === "user" ? (
        <CommentForm
          collectionId={collectionId}
          onPosted={(c) => patch((d) => prependComment(d, c))}
        />
      ) : (
        <p className="text-ink-muted" data-testid="comments-login">
          <Link href="/login" className="underline underline-offset-4">
            {t("social.comments.login")}
          </Link>{" "}
          {t("social.comments.loginTail")}
        </p>
      )}
      <p className="tabular text-sm text-ink-muted" data-testid="comments-total">
        {t("social.comments.total", { count: total })}
      </p>
      {items.length === 0 ? (
        <p className="text-ink-muted" data-testid="comments-empty">
          {t("social.comments.empty")}
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((c) => (
            <CommentRow
              key={c.id}
              comment={c}
              mine={!!myId && c.author_id === myId}
              canReport={viewer.kind === "user" && c.author_id !== myId}
              onEdited={(x) => patch((d) => replaceComment(d, x))}
              onDeleted={() => patch((d) => removeComment(d, c.id))}
            />
          ))}
        </ul>
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          className="self-center"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
          data-testid="comments-more"
        >
          {t("social.comments.more")}
        </Button>
      )}
      {query.isFetchNextPageError && (
        <p role="alert" className="text-sm text-miss">
          {t("social.comments.moreError")}
        </p>
      )}
    </>
  );
}

function CommentForm({
  collectionId,
  onPosted,
}: {
  collectionId: string;
  onPosted: (c: CommentView) => void;
}) {
  const t = useTranslations();
  const action = useSocialAction();
  const [text, setText] = useState("");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    // Ключ привязан к тексту: повтор после сетевого сбоя с тем же текстом не плодит дубль.
    const r = await action.run(`post:${collectionId}:${body}`, (key) =>
      postComment(collectionId, body, key),
    );
    if (!r.ok) return;
    setText("");
    onPosted(r.value);
  };

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2">
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        {t("social.comments.write")}
        <textarea
          value={text}
          maxLength={COMMENT_MAX_LENGTH}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          data-testid="comment-text"
          className="rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-3 font-normal text-ink outline-none focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)]"
        />
      </label>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="tabular text-xs text-ink-faint" data-testid="comment-counter">
          {text.length}/{COMMENT_MAX_LENGTH}
        </span>
        <Button type="submit" disabled={action.busy || !text.trim()} data-testid="comment-submit">
          {t("social.comments.send")}
        </Button>
      </div>
      <ErrorLine text={action.error} />
    </form>
  );
}

function CommentRow({
  comment,
  mine,
  canReport,
  onEdited,
  onDeleted,
}: {
  comment: CommentView;
  mine: boolean;
  canReport: boolean;
  onEdited: (c: CommentView) => void;
  onDeleted: () => void;
}) {
  const t = useTranslations();
  const f = useFormatter();
  const action = useSocialAction();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(comment.body);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body = draft.trim();
    if (!body) return;
    const r = await action.run(`edit:${comment.id}`, () => editComment(comment.id, body));
    if (!r.ok) return;
    setEditing(false);
    onEdited(r.value);
  };
  const remove = async () => {
    const r = await action.run(`delete:${comment.id}`, () => deleteComment(comment.id));
    if (r.ok) onDeleted();
  };

  return (
    <li
      data-testid="comment"
      data-mine={mine}
      className="flex flex-col gap-2 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-4"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span dir="auto" className="font-medium">
          {comment.author_nickname}
        </span>
        <time dateTime={comment.created_at} className="text-xs text-ink-faint">
          {f.dateTime(new Date(comment.created_at), { dateStyle: "medium", timeStyle: "short" })}
        </time>
        {comment.edited_at && (
          <span className="text-xs text-ink-faint">{t("social.comments.edited")}</span>
        )}
      </div>
      {editing ? (
        <form onSubmit={(e) => void save(e)} className="flex flex-col gap-2">
          <textarea
            value={draft}
            maxLength={COMMENT_MAX_LENGTH}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            aria-label={t("social.comments.editLabel")}
            data-testid="comment-edit-text"
            className="rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-3 text-ink outline-none focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)]"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" disabled={action.busy || !draft.trim()}>
              {t("social.comments.save")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setDraft(comment.body);
              }}
            >
              {t("social.cancel")}
            </Button>
            <span className="tabular ml-auto text-xs text-ink-faint">
              {draft.length}/{COMMENT_MAX_LENGTH}
            </span>
          </div>
        </form>
      ) : (
        <p dir="auto" className="break-words whitespace-pre-line" data-testid="comment-body">
          {comment.body}
        </p>
      )}
      {!editing && (comment.can_edit || comment.can_delete || canReport) && (
        <div className="flex flex-wrap items-center gap-1">
          {comment.can_edit && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setEditing(true)}
              data-testid="comment-edit"
            >
              {t("social.comments.edit")}
            </Button>
          )}
          {comment.can_delete && (
            <Button
              size="sm"
              variant="ghost"
              disabled={action.busy}
              onClick={() => void remove()}
              data-testid="comment-delete"
            >
              {t("social.comments.delete")}
            </Button>
          )}
          {canReport && (
            <ReportButton
              kind="comment"
              targetId={comment.id}
              ariaLabel={t("social.report.commentLabel", { name: comment.author_nickname })}
              testId="comment-report"
            />
          )}
        </div>
      )}
      <ErrorLine text={action.error} />
    </li>
  );
}
