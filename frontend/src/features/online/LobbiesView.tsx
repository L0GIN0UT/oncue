"use client";
/** `/lobbies`: вход по коду и все открытые лобби (match `/v1/rooms/open`, keyset-курсор). */
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { Button } from "@/shared/ui/button";
import { HubHeader } from "@/features/hubs/HubHeader";
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
    <div className="flex flex-col gap-5 pt-4">
      <HubHeader title={t("online.lobbies.title")} />
      <p className="px-0.5 text-sm text-ink-muted">{t("online.lobbies.lead")}</p>
      <JoinByCode primary />
      <section aria-label={t("online.lobbies.title")} className="flex flex-col gap-3.5">
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
