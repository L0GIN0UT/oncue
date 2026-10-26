"use client";
/**
 * «Мои сборники» `/me/collections` (D4): черновики и опубликованные (по `GET /v1/me/collections`
 * с курсором), плашка «скрыт модератором», переход в конструктор и удаление с подтверждением.
 */
import { useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { Pencil, Trash2 } from "lucide-react";
import type { MyCollection } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { TabPanel, Tabs } from "@/shared/ui/tabs";
import { useToast } from "@/shared/ui/toast";
import { useViewer } from "@/features/auth/session";
import { HubHeader } from "@/features/hubs/HubHeader";
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
    <div className="flex flex-col gap-4 pt-4">
      <HubHeader
        title={t("collections.mine.title")}
        aside={
          <Link href="/collections" className="hover:text-ink">
            {t("collections.title")}
          </Link>
        }
      />
      <Tabs
        aria-label={t("collections.mine.title")}
        idBase="my-collections"
        value={tab}
        onChange={setTab}
        tabs={(["published", "drafts"] as const).map((k) => ({
          value: k,
          label: t(`collections.mine.${k}`, {
            count: k === "drafts" ? drafts.length : published.length,
          }),
        }))}
      />
      <TabPanel idBase="my-collections" value={tab} active>
        {list.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : list.isError ? (
          <p className="text-ink-muted">{t("collections.unavailable")}</p>
        ) : shown.length === 0 ? (
          <p className="text-ink-muted">{t(`collections.mine.empty.${tab}`)}</p>
        ) : (
          <ul className="flex flex-col gap-2.5">
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
      </TabPanel>
      <div className="flex justify-end pt-1.5">
        <Button asChild>
          <Link href="/collections/new">{t("collections.createOwn")}</Link>
        </Button>
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
      className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex min-w-0 flex-col gap-1">
        <p className="text-[13px] text-ink-muted">
          {t(`domain.${item.domain}`)} · {t(`collections.status.${item.status}`)}
          {item.has_draft && item.status !== "draft" ? ` · ${t("collections.mine.hasDraft")}` : ""}
        </p>
        {item.status === "draft" ? (
          <p dir="auto" className="truncate font-semibold">
            {item.title || t("collections.untitled")}
          </p>
        ) : (
          <Link
            href={`/collections/${item.collection_id}`}
            dir="auto"
            className="truncate font-semibold hover:underline"
          >
            {item.title || t("collections.untitled")}
          </Link>
        )}
        <p className="tabular text-[13px] text-ink-muted">
          {t("collections.itemsCount", { count: item.items_count })} ·{" "}
          {t("collections.mine.likes", { count: item.likes_count })} ·{" "}
          {format.dateTime(new Date(item.updated_at), { day: "numeric", month: "short" })}
        </p>
        {item.hidden_by_moderator && (
          <p
            className="self-start rounded-full border-[1.5px] border-miss-line bg-miss-fill px-3 py-1 text-xs"
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
