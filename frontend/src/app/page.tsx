import Link from "next/link";
import { getTranslations } from "next-intl/server";
import { fetchHomePage } from "@/shared/api/server";
import { DailyBoard } from "@/features/daily/DailyCards";
import { HomeProgress } from "@/features/progression/HomeProgress";

/** Главная: SSR — каркас и публичные блоки bff, CSR — личный статус дейликов и отсчёт (D2). */
export default async function HomePage() {
  const [t, page] = await Promise.all([getTranslations(), fetchHomePage()]);
  return (
    <div className="flex flex-col gap-14 pt-6 sm:pt-12">
      <section className="flex max-w-3xl flex-col gap-5">
        <h1 className="font-display text-4xl leading-[1.05] font-bold tracking-tight text-balance sm:text-6xl">
          {t("home.headline")}
        </h1>
        <p className="max-w-[56ch] text-lg text-ink-muted">{t("home.lead")}</p>
      </section>
      <HomeProgress initial={page} />
      <DailyBoard initial={page} />
      <section className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage/60 p-6 ring-1 ring-rule sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold">{t("home.moreTitle")}</h2>
          <p className="text-ink-muted">{t("home.moreText")}</p>
        </div>
        <Link
          href="/solo"
          className="self-start rounded-[var(--radius-control)] px-4 py-2.5 font-medium text-ink ring-1 ring-rule ring-inset hover:bg-stage-raised sm:self-auto"
        >
          {t("home.moreAction")}
        </Link>
      </section>
    </div>
  );
}
