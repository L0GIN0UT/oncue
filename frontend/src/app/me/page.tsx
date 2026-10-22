import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { MyMatchesView } from "@/features/match/MyMatchesView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("online.myMatches.title"), robots: { index: false } };
}

export default function MePage() {
  return <MyMatchesView />;
}
