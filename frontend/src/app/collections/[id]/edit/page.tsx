import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { safeDecode } from "@/shared/lib/decode";
import { CollectionEditorPage } from "@/features/collections/editor/CollectionEditor";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.editor.title"), robots: { index: false } };
}

export default async function EditCollectionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CollectionEditorPage key={id} collectionId={safeDecode(id)} />;
}
