import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { NewCollection } from "@/features/collections/editor/NewCollection";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.new.title"), robots: { index: false } };
}

export default function NewCollectionPage() {
  return <NewCollection />;
}
