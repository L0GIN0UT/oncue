"use client";
/**
 * Полка `/library/{ref}` (D6): единицы по курсору (новые сверху), заблокированная в каталоге
 * единица остаётся с отметкой `unavailable`, кнопка «Сохранить» переносит между полками.
 */
import Link from "next/link";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import { Button } from "@/shared/ui/button";
import { useViewer } from "@/features/auth/session";
import { fetchShelfPage, libraryKeys } from "./api";
import { LibraryGate } from "./LibraryView";
import { SaveButton } from "./SaveButton";
import { useShelfTitle } from "./ShelfPicker";

export function ShelfView({ shelfRef }: { shelfRef: string }) {
  const t = useTranslations();
  const viewer = useViewer();
  const title = useShelfTitle();
  const list = useInfiniteQuery({
    queryKey: libraryKeys.shelf(shelfRef),
    queryFn: ({ pageParam }) => fetchShelfPage(shelfRef, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) =>
      last.blocks.shelf.status === "ok" ? (last.blocks.shelf.data?.next_cursor ?? null) : null,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <LibraryGate status={viewer.kind === "guest" ? 403 : 401} />;
  if (list.isError) {
    const status = isApiError(list.error) ? list.error.status : null;
    if (status === 401 || status === 403) return <LibraryGate status={status} />;
    return (
      <div className="flex flex-col items-start gap-4 pt-12">
        <h1 className="font-display text-2xl font-bold">
          {t(status === 404 ? "library.notFound" : "library.unavailable")}
        </h1>
        <Button asChild variant="secondary">
          <Link href="/library">{t("library.title")}</Link>
        </Button>
      </div>
    );
  }
  if (!list.data)
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  const first = list.data.pages[0]?.blocks.shelf;
  if (!first || first.status !== "ok" || !first.data)
    return <p className="pt-12 text-ink-muted">{t("library.unavailable")}</p>;
  const shelf = first.data.shelf;
  const items = list.data.pages.flatMap((p) => p.blocks.shelf.data?.items ?? []);

  return (
    <div className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <Link href="/library" className="text-sm text-ink-muted hover:text-ink">
          ← {t("library.title")}
        </Link>
        <h1 dir="auto" className="font-display text-3xl font-bold">
          {title(shelf)}
        </h1>
        <p className="text-ink-muted">{t("library.count", { count: shelf.items_count })}</p>
      </header>
      {items.length === 0 ? (
        <p className="text-ink-muted">{t("library.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li
              key={item.media_item_id}
              data-testid={`library-item-${item.media_item_id}`}
              data-domain={item.domain}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-stage px-4 py-3 ring-1 ring-rule"
            >
              <div className="min-w-0">
                <p dir="auto" className="truncate font-medium">
                  {item.title}
                </p>
                <p dir="auto" className="truncate text-sm text-ink-muted">
                  {[item.year, ...(item.persons ?? [])].filter(Boolean).join(", ")}
                </p>
                {item.status === "unavailable" && (
                  <p
                    className="text-sm text-miss"
                    data-testid={`library-item-unavailable-${item.media_item_id}`}
                  >
                    {t("library.unavailableItem")}
                  </p>
                )}
              </div>
              <SaveButton mediaItemId={item.media_item_id} domain={item.domain} source="library" />
            </li>
          ))}
        </ul>
      )}
      {list.hasNextPage && (
        <Button
          variant="secondary"
          className="self-center"
          disabled={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          {t("collections.more")}
        </Button>
      )}
    </div>
  );
}
