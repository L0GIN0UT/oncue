import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { EventSpot } from "@/features/events/EventSpot";
import { MatchResultView } from "@/features/match/MatchResultView";
import { safeDecode } from "@/shared/lib/decode";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("match.summary.title"), robots: { index: false } };
}

export default async function MatchPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <>
      <MatchResultView matchId={safeDecode(id)} />
      <EventSpot pageKey="match_summary" />
    </>
  );
}
