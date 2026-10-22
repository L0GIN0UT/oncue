import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { LobbiesView } from "@/features/online/LobbiesView";
import { OnlineGate } from "@/features/online/OnlineGate";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("online.lobbies.title"), robots: { index: false } };
}

export default function LobbiesPage() {
  return (
    <OnlineGate>
      <LobbiesView />
    </OnlineGate>
  );
}
