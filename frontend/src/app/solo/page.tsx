import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";
import { fetchSoloPage } from "@/shared/api/server";
import { SoloHub } from "@/features/solo/SoloHub";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations();
  return { title: t("solo.title") };
}

export default async function SoloPage() {
  const [t, page] = await Promise.all([getTranslations(), fetchSoloPage()]);
  return (
    <div className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex max-w-2xl flex-col gap-3">
        <h1 className="font-display text-3xl font-bold tracking-tight sm:text-5xl">
          {t("solo.title")}
        </h1>
        <p className="text-lg text-ink-muted">{t("solo.lead")}</p>
      </header>
      <SoloHub initial={page} />
    </div>
  );
}
