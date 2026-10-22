"use client";
/** `/lobbies`: вход по коду и все открытые лобби (match `/v1/rooms/open`, keyset-курсор). */
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { Button } from "@/shared/ui/button";
import { JoinByCode } from "./JoinByCode";
import { OpenRoomList } from "./lists";

export function LobbiesView() {
  const t = useTranslations();
  const query = useInfiniteQuery({
    queryKey: ["match", "rooms", "open"],
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) =>
      unwrap(
        await api().match.GET("/v1/rooms/open", {
          params: { query: pageParam ? { cursor: pageParam } : {} },
        }),
      ),
    getNextPageParam: (last) => last.next_cursor ?? null,
    refetchInterval: 15_000,
  });
  const rooms = query.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex flex-col gap-3">
        <h1 className="font-display text-3xl leading-tight font-bold">
          {t("online.lobbies.title")}
        </h1>
        <p className="max-w-[60ch] text-ink-muted">{t("online.lobbies.lead")}</p>
      </header>
      <div className="max-w-xl">
        <JoinByCode />
      </div>
      <section aria-label={t("online.lobbies.title")} className="flex flex-col gap-4">
        {query.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : query.isError ? (
          <p className="text-ink-muted" role="alert">
            {t("online.blockUnavailable")}
          </p>
        ) : (
          <OpenRoomList rooms={rooms} />
        )}
        {query.hasNextPage && (
          <Button
            variant="secondary"
            className="self-start"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {t("online.showMore")}
          </Button>
        )}
      </section>
    </div>
  );
}
