import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { OnlineGate } from "@/features/online/OnlineGate";
import { OnlineHub } from "@/features/online/OnlineHub";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("online.title"), robots: { index: false } };
}

export default function OnlinePage() {
  return (
    <OnlineGate>
      <OnlineHub />
    </OnlineGate>
  );
}
