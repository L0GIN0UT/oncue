import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { ResultsView } from "@/features/results/ResultsView";

export const dynamic = "force-dynamic";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("results.title"), robots: { index: false } };
}

export default async function ResultsPage({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <ResultsView sessionId={sessionId} />;
}
