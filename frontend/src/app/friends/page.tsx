import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { FriendsView } from "@/features/friends/FriendsView";
import { tabFromParam } from "@/features/friends/tabs";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("social.title"), robots: { index: false } };
}

export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams;
  return <FriendsView initialTab={tabFromParam(tab)} />;
}
