import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { MyCollections } from "@/features/collections/MyCollections";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.mine.title"), robots: { index: false } };
}

export default function MyCollectionsPage() {
  return <MyCollections />;
}
