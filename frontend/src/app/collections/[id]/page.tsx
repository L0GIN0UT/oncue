import { Suspense } from "react";
import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { safeDecode } from "@/shared/lib/decode";
import { CatalogView } from "@/features/collections/catalog/CatalogView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("collections.pageTitle") };
}

/** Сборник — модалка поверх каталога; прямая ссылка открывает её сразу. */
export default async function CollectionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <Suspense>
      <CatalogView key={id} openId={safeDecode(id)} />
    </Suspense>
  );
}
