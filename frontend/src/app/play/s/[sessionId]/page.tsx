import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { GameScreen } from "@/features/game/GameScreen";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("game.title"), robots: { index: false } };
}

export default async function SessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <GameScreen key={sessionId} sessionId={sessionId} />;
}
