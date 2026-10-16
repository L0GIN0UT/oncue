import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { safeDecode } from "@/shared/lib/decode";
import { CollectionView } from "@/features/collections/catalog/CollectionView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.pageTitle") };
}

export default async function CollectionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CollectionView key={id} collectionId={safeDecode(id)} />;
}
