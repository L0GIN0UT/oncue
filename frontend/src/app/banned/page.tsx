import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { BannedView } from "@/features/auth/BannedView";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("auth.banned.title"), robots: { index: false } };
}

export default function Page() {
  return <BannedView />;
}
