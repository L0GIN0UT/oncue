import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { parseRounds, StartGame } from "@/features/game/StartGame";

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
  // Сборник (этап 3b): id сверяет game; здесь — только форма uuid, чтобы не слать мусор.
  const collectionId = one(query.collection);
  const collection =
    collectionId && /^[0-9a-f-]{36}$/i.test(collectionId)
      ? { collectionId, rounds: parseRounds(one(query.rounds)) }
      : null;
  return (
    <StartGame modeKey={decodeURIComponent(modeKey)} presetId={preset} collection={collection} />
  );
}
