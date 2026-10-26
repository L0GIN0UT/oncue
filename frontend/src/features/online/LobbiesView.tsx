"use client";
/**
 * `/lobbies` по макету v3 (Lobbies): фильтр и поиск, открытые лобби слева (match
 * `/v1/rooms/open`, keyset-курсор, обновляются сами), справа — создание лобби и вход по коду.
 * Фильтры и поиск работают по уже загруженным страницам.
 */
import Link from "next/link";
import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { Button } from "@/shared/ui/button";
import { PillTabs } from "@/shared/ui/pill-tabs";
import { Tile } from "@/shared/ui/tile";
import { useIcons } from "@/shared/ui/theme-provider";
import { SearchField } from "@/features/hubs/SearchField";
import { blockData, useOnlinePage } from "./bff";
import { CreateRoom } from "./CreateRoom";
import { JoinByCode } from "./JoinByCode";
import { OpenRoomList, useModeTitle } from "./lists";

type DomainFilter = "all" | "music" | "film";

export function LobbiesView() {
  const t = useTranslations();
  const icons = useIcons();
  const modeTitle = useModeTitle();
  const online = useOnlinePage();
  const [domain, setDomain] = useState<DomainFilter>("all");
  const [search, setSearch] = useState("");
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
  const all = query.data?.pages.flatMap((p) => p.items) ?? [];
  const needle = search.trim().toLowerCase();
  const rooms = all.filter(
    (r) =>
      (domain === "all" || r.mode.domain === domain) &&
      (!needle ||
        r.host_nickname.toLowerCase().includes(needle) ||
        modeTitle(r.mode).toLowerCase().includes(needle)),
  );
  const modes = online.data?.status === "ok" ? blockData(online.data.blocks?.modes) : null;
  const Back = icons.back;

  return (
    <div className="flex flex-col gap-5 pt-2" data-testid="lobbies">
      <h1 className="sr-only">{t("online.lobbies.title")}</h1>
      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/online"
          className="inline-flex h-9 items-center gap-1.5 rounded-full border-[1.5px] border-rule bg-stage px-3.5 text-[13px] font-semibold hover:border-rule-strong"
        >
          <Back aria-hidden className="size-3.5" />
          {t("online.lobbies.back")}
        </Link>
        <PillTabs
          aria-label={t("online.lobbies.filter")}
          tabs={[
            { value: "all", label: t("online.lobbies.filterAll") },
            { value: "music", label: t("domain.music") },
            { value: "film", label: t("domain.film") },
          ]}
          value={domain}
          onChange={setDomain}
        />
        <SearchField
          aria-label={t("online.lobbies.search")}
          placeholder={t("online.lobbies.search")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-9 text-[13px]"
          wrapperClassName="ml-auto w-full sm:w-[300px]"
        />
      </div>

      <section className="grid items-start gap-5 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Tile
          cap={t("online.lobbies.cap", { count: rooms.length })}
          aside={
            <span className="rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-exact">
              {t("online.lobbies.live")}
            </span>
          }
          data-testid="lobbies-list"
        >
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
          <div className="mt-auto flex items-center justify-between gap-3">
            <span className="text-xs text-ink-muted">
              {t("online.lobbies.foot", { count: rooms.length })}
            </span>
            {query.hasNextPage && (
              <Button
                variant="secondary"
                size="sm"
                disabled={query.isFetchingNextPage}
                onClick={() => void query.fetchNextPage()}
              >
                {t("online.showMore")}
              </Button>
            )}
          </div>
        </Tile>

        <div className="flex flex-col gap-5">
          <Tile
            cap={t("online.lobbies.createCap")}
            aside={
              <span className="rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted">
                {t("online.room.upTo")}
              </span>
            }
            data-testid="lobbies-create"
          >
            <CreateRoom modes={modes} instant={false} />
          </Tile>
          <Tile cap={t("online.join.title")} data-testid="lobbies-join">
            <JoinByCode />
          </Tile>
        </div>
      </section>
    </div>
  );
}
