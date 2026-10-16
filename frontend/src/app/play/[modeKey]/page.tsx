import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { StartGame } from "@/features/game/StartGame";
import { parseCollectionStart } from "@/features/game/start-params";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("start.title"), robots: { index: false } };
}

export default async function PlayModePage({
  params,
  searchParams,
}: {
  params: Promise<{ modeKey: string }>;
  searchParams: Promise<{
    preset?: string | string[];
    collection?: string | string[];
    rounds?: string | string[];
  }>;
}) {
  const [{ modeKey }, query] = await Promise.all([params, searchParams]);
  const one = (v: string | string[] | undefined) => (typeof v === "string" && v ? v : null);
  const preset = one(query.preset);
  const collection = parseCollectionStart(one(query.collection), one(query.rounds));
  return (
    <StartGame modeKey={decodeURIComponent(modeKey)} presetId={preset} collection={collection} />
  );
}
