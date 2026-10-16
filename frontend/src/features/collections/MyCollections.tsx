"use client";
/**
 * «Мои сборники» `/me/collections` (D4): черновики и опубликованные (по `GET /v1/me/collections`
 * с курсором), плашка «скрыт модератором», переход в конструктор и удаление с подтверждением.
 */
import { useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { Pencil, Plus, Trash2 } from "lucide-react";
import type { MyCollection } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useViewer } from "@/features/auth/session";
import { collectionKeys, deleteCollection, fetchMyCollections } from "./api";
import { apiErrorText } from "./errors";
import { UsersOnly } from "./editor/CollectionEditor";

type Tab = "drafts" | "published";

/** Черновик — ещё не опубликованный сборник; опубликованный (в т. ч. скрытый) — с версией. */
export const isDraftOnly = (c: MyCollection) => c.status === "draft";

export function MyCollections() {
  const t = useTranslations();
  const viewer = useViewer();
  const [tab, setTab] = useState<Tab>("published");
  const list = useInfiniteQuery({
    queryKey: collectionKeys.mine,
    queryFn: ({ pageParam }) => fetchMyCollections(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user") return <UsersOnly />;

  const all = list.data?.pages.flatMap((p) => p.items) ?? [];
  const drafts = all.filter(isDraftOnly);
  const published = all.filter((c) => !isDraftOnly(c));
  const shown = tab === "drafts" ? drafts : published;

  return (
    <div className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="font-display text-3xl font-bold">{t("collections.mine.title")}</h1>
        <Button asChild>
          <Link href="/collections/new">
            <Plus aria-hidden />
            {t("collections.create")}
          </Link>
        </Button>
      </header>
      <div role="tablist" aria-label={t("collections.mine.title")} className="flex gap-2">
        {(["published", "drafts"] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={tab === k}
            onClick={() => setTab(k)}
            data-testid={`my-collections-tab-${k}`}
            className={cn(
              "rounded-full px-4 py-2 text-sm ring-1 ring-rule",
              tab === k ? "bg-stage-raised text-ink" : "text-ink-muted",
            )}
          >
            {t(`collections.mine.${k}`, {
              count: k === "drafts" ? drafts.length : published.length,
            })}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {list.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : list.isError ? (
          <p className="text-ink-muted">{t("collections.unavailable")}</p>
        ) : shown.length === 0 ? (
          <p className="text-ink-muted">{t(`collections.mine.empty.${tab}`)}</p>
        ) : (
          <ul className="flex flex-col gap-3">
            {shown.map((c) => (
              <MyCollectionRow key={c.collection_id} item={c} />
            ))}
          </ul>
        )}
        {list.hasNextPage && (
          <Button
            variant="secondary"
            className="mt-4"
            disabled={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
          >
            {t("collections.more")}
          </Button>
        )}
      </div>
    </div>
  );
}

function MyCollectionRow({ item }: { item: MyCollection }) {
  const t = useTranslations();
  const format = useFormatter();
  const toast = useToast();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  const remove = async () => {
    setPending(true);
    try {
      await deleteCollection(item.collection_id);
      await client.invalidateQueries({ queryKey: collectionKeys.mine });
    } catch (e) {
      toast(apiErrorText(t, e));
      setPending(false);
    }
  };

  return (
    <li
      data-testid={`my-collection-${item.collection_id}`}
      data-domain={item.domain}
      className="flex flex-col gap-3 rounded-xl bg-stage px-4 py-4 ring-1 ring-rule sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-xs text-[var(--accent)]">
          {t(`domain.${item.domain}`)} · {t(`collections.status.${item.status}`)}
          {item.has_draft && item.status !== "draft" ? ` · ${t("collections.mine.hasDraft")}` : ""}
        </p>
        {item.status === "draft" ? (
          <p dir="auto" className="truncate font-medium">
            {item.title || t("collections.untitled")}
          </p>
        ) : (
          <Link
            href={`/collections/${item.collection_id}`}
            dir="auto"
            className="truncate font-medium hover:underline"
          >
            {item.title || t("collections.untitled")}
          </Link>
        )}
        <p className="tabular text-sm text-ink-muted">
          {t("collections.itemsCount", { count: item.items_count })} ·{" "}
          {t("collections.mine.likes", { count: item.likes_count })} ·{" "}
          {format.dateTime(new Date(item.updated_at), { day: "numeric", month: "short" })}
        </p>
        {item.hidden_by_moderator && (
          <p
            className="self-start rounded-full bg-miss/15 px-3 py-1 text-xs text-miss ring-1 ring-miss"
            data-testid="my-collection-hidden"
          >
            {t("collections.hiddenByModerator")}
          </p>
        )}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button asChild variant="secondary" size="sm">
          <Link href={`/collections/${item.collection_id}/edit`}>
            <Pencil aria-hidden />
            {t("collections.edit")}
          </Link>
        </Button>
        {confirming ? (
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={pending}
              onClick={() => void remove()}
              data-testid="my-collection-delete-confirm"
            >
              {t("collections.mine.deleteConfirm")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
              {t("collections.mine.cancel")}
            </Button>
          </>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setConfirming(true)}
            data-testid="my-collection-delete"
          >
            <Trash2 aria-hidden />
            {t("collections.mine.delete")}
          </Button>
        )}
      </div>
    </li>
  );
}
