import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { EventSpot } from "@/features/events/EventSpot";
import { ProfileView } from "@/features/progression/ProfileView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("progression.title"), robots: { index: false } };
}

export default function ProfilePage() {
  return (
    <>
      <ProfileView />
      <EventSpot pageKey="profile" />
    </>
  );
}
