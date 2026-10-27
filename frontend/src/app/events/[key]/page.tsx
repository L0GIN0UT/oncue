import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { fetchHomePage } from "@/shared/api/server";
import { EventDialog } from "@/features/events/EventDialog";
import { HomeView } from "@/features/home/HomeView";
import { safeDecode } from "@/shared/lib/decode";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("events.title"), robots: { index: false } };
}

/** Прямая ссылка на событие: главная под модалкой дневника (та же, что открывает плитка). */
export default async function EventPage({ params }: { params: Promise<{ key: string }> }) {
  const { key } = await params;
  const page = await fetchHomePage();
  return (
    <>
      <HomeView initial={page} />
      <EventDialog eventKey={safeDecode(key)} />
    </>
  );
}
