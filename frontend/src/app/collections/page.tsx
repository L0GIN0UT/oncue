import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { CatalogView } from "@/features/collections/catalog/CatalogView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.title"), description: t("collections.lead") };
}

export default function CollectionsPage() {
  return (
    <Suspense>
      <CatalogView />
    </Suspense>
  );
}
