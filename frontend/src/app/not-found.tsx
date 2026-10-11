import Link from "next/link";
import { getTranslations } from "next-intl/server";

export default async function NotFound() {
  const t = await getTranslations();
  return (
    <div className="flex flex-col items-start gap-4 pt-16">
      <h1 className="font-display text-3xl font-bold">{t("notFound.title")}</h1>
      <p className="text-ink-muted">{t("notFound.text")}</p>
      <Link href="/" className="font-medium text-music underline-offset-4 hover:underline">
        {t("common.home")}
      </Link>
    </div>
  );
}
