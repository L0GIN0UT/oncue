import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { NotificationsView } from "@/features/notifications/NotificationsView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("notifications.title"), robots: { index: false } };
}

export default function Page() {
  return <NotificationsView />;
}
