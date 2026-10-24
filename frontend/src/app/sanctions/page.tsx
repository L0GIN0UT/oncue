import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { SanctionsView } from "@/features/sanctions/SanctionsView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("social.sanctions.title"), robots: { index: false } };
}

export default function Page() {
  return <SanctionsView />;
}
