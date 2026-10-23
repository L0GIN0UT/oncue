"use client";
/**
 * Профиль `/me`: «Мои матчи» (match `/v1/me/matches`, новые первыми, keyset-курсор) и ссылка
 * на мои сборники. Только пользователю — гостю экран регистрации (D13).
 */
import Link from "next/link";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { useSessionInfo } from "@/features/auth/session";
import { blockData, hasBlock, useOnlinePage } from "@/features/online/bff";
import { MyMatchList } from "@/features/online/lists";
import { RatingPanel } from "@/features/rating/RatingPanel";
import { OnlineGate } from "@/features/online/OnlineGate";
import { fetchMyMatches } from "./api";

export function MyMatchesView() {
  return (
    <OnlineGate>
      <MyProfile />
    </OnlineGate>
  );
}

function MyProfile() {
  const t = useTranslations();
  const session = useSessionInfo();
  const query = useInfiniteQuery({
    queryKey: ["match", "me", "matches"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchMyMatches(pageParam),
    getNextPageParam: (last) => last.next_cursor ?? null,
  });
  const matches = query.data?.pages.flatMap((p) => p.items) ?? [];
  // Звание и сезон — из блоков 4b хаба (тот же запрос, что у /online, кэш общий).
  const online = useOnlinePage();
  const blocks = online.data?.status === "ok" ? online.data.blocks : null;

  return (
    <div className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <h1 className="font-display text-3xl font-bold" dir="auto">
          {session.data?.subject?.display_name ?? t("nav.player")}
        </h1>
        <Link
          href="/me/collections"
          className="text-ink-muted underline-offset-4 hover:text-ink hover:underline"
        >
          {t("nav.myCollections")}
        </Link>
      </header>
      {hasBlock(blocks?.ratings) && (
        <section aria-labelledby="me-ratings" className="flex flex-col gap-4">
          <h2 id="me-ratings" className="font-display text-xl font-semibold">
            {t("rating.mine.title")}
          </h2>
          <RatingPanel ratings={blockData(blocks?.ratings)} seasons={blockData(blocks?.seasons)} />
        </section>
      )}
      <section aria-labelledby="me-matches" className="flex flex-col gap-4">
        <h2 id="me-matches" className="font-display text-xl font-semibold">
          {t("online.myMatches.title")}
        </h2>
        {query.isPending ? (
          <p className="text-ink-muted" role="status">
            {t("common.loading")}
          </p>
        ) : query.isError ? (
          <p className="text-ink-muted" role="alert">
            {t("online.blockUnavailable")}
          </p>
        ) : (
          <MyMatchList matches={matches} />
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
