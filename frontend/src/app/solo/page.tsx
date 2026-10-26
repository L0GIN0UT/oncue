import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { fetchSoloPage } from "@/shared/api/server";
import { SoloHub } from "@/features/solo/SoloHub";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("solo.title") };
}

export default async function SoloPage() {
  const page = await fetchSoloPage();
  return <SoloHub initial={page} />;
}
