import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { EventSpot } from "@/features/events/EventSpot";
import { LeaderboardsView } from "@/features/rating/LeaderboardsView";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("rating.board.title") };
}

export default function LeaderboardsPage() {
  return (
    <>
      <LeaderboardsView />
      <EventSpot pageKey="leaderboards" />
    </>
  );
}
