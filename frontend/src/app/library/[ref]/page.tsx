import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { safeDecode } from "@/shared/lib/decode";
import { ShelfView } from "@/features/library/ShelfView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("library.title"), robots: { index: false } };
}

export default async function ShelfPage({ params }: { params: Promise<{ ref: string }> }) {
  const { ref } = await params;
  // ref системной полки — `system:<key>`: двоеточие в адресе может прийти закодированным.
  const shelfRef = safeDecode(ref);
  return <ShelfView key={shelfRef} shelfRef={shelfRef} />;
}
