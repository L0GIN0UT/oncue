import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { RoomScreen } from "@/features/match/RoomScreen";
import { safeDecode } from "@/shared/lib/decode";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("match.lobby.title"), robots: { index: false } };
}

export default async function RoomPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  return <RoomScreen code={safeDecode(code)} />;
}
