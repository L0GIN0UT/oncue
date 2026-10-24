import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { NotificationSettingsView } from "@/features/notifications/NotificationSettingsView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("notifications.settings.title"), robots: { index: false } };
}

export default function Page() {
  return <NotificationSettingsView />;
}
