import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { PlayerView } from "@/features/progression/PlayerView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("progression.player.title"), robots: { index: false } };
}

export default async function PlayerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <PlayerView userId={id} />;
}
