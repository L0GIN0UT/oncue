import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { EventView } from "@/features/events/EventView";
import { safeDecode } from "@/shared/lib/decode";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("events.title"), robots: { index: false } };
}

export default async function EventPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  return <EventView eventKey={safeDecode(key)} />;
}
