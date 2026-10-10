import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { StartGame } from "@/features/game/StartGame";

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
  searchParams: Promise<{ preset?: string | string[] }>;
}) {
  const [{ modeKey }, query] = await Promise.all([params, searchParams]);
  const preset = typeof query.preset === "string" && query.preset ? query.preset : null;
  return <StartGame modeKey={decodeURIComponent(modeKey)} presetId={preset} />;
}
