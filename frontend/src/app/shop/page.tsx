import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ShopView } from "@/features/shop/ShopView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("shop.title"), robots: { index: false } };
}

export default function ShopPage() {
  return <ShopView />;
}
