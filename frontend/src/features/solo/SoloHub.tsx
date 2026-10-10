"use client";
/** Соло-хаб: дейлики (личный статус) и случайные режимы с пресетами по доменам. */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import type { Domain, ModeCard, SoloPage } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { DailyTicket } from "@/features/daily/DailyCards";

export function SoloHub({ initial }: { initial: SoloPage | null }) {
  const t = useTranslations();
  const { data } = useQuery({
    queryKey: ["page", "solo"],
    queryFn: async (): Promise<SoloPage> => unwrap(await api().web.GET("/v1/pages/solo")),
    initialData: initial ?? undefined,
  });
  const daily = data?.blocks.daily;
  const random = data?.blocks.random;
  const domains: Domain[] = ["music", "film"];
  return (
    <div className="flex flex-col gap-12">
      <section aria-labelledby="solo-daily" className="flex flex-col gap-5">
        <h2 id="solo-daily" className="font-display text-xl font-semibold">
          {t("daily.title")}
        </h2>
        {daily?.status === "ok" ? (
          <ul className="grid gap-5 md:grid-cols-2">
            {(daily.data ?? []).map((card) => (
              <li key={card.mode_key}>
                <DailyTicket card={card} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-ink-muted">{t("daily.unavailable")}</p>
        )}
      </section>
      <section aria-labelledby="solo-random" className="flex flex-col gap-5">
        <div>
          <h2 id="solo-random" className="font-display text-xl font-semibold">
            {t("solo.randomTitle")}
          </h2>
          <p className="mt-1 text-ink-muted">{t("solo.randomText")}</p>
        </div>
        {random?.status === "ok" ? (
          <div className="grid gap-5 md:grid-cols-2">
            {domains.map((domain) =>
              (random.data ?? [])
                .filter((m) => m.domain === domain)
                .map((mode) => <RandomMode key={mode.key} mode={mode} />),
            )}
          </div>
        ) : (
          <p className="text-ink-muted">{t("solo.randomUnavailable")}</p>
        )}
      </section>
    </div>
  );
}

function RandomMode({ mode }: { mode: ModeCard }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const chip =
    "inline-flex h-10 items-center rounded-full px-4 text-sm font-medium ring-1 ring-inset transition-colors";
  return (
    <article
      data-domain={mode.domain}
      data-testid={`random-${mode.key}`}
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule"
    >
      <div>
        <p className="text-sm text-[var(--accent)]">{t(`domain.${mode.domain}`)}</p>
        <h3 className="mt-1 font-display text-xl font-bold">{dt(mode.title_key, mode.key)}</h3>
        <p className="mt-1 text-sm text-ink-muted">{t("solo.rounds", { count: mode.rounds })}</p>
      </div>
      <ul className="flex flex-wrap gap-2" aria-label={t("solo.presets")}>
        <li>
          <Link
            href={`/play/${mode.key}`}
            className={`${chip} bg-[var(--accent)] text-night ring-transparent hover:brightness-110`}
          >
            {t("solo.anyPreset")}
          </Link>
        </li>
        {(mode.presets ?? []).map((p) => (
          <li key={p.id}>
            <Link
              href={`/play/${mode.key}?preset=${encodeURIComponent(p.id)}`}
              className={`${chip} text-ink ring-rule hover:bg-stage-raised`}
            >
              {dt(p.title_key, p.id)}
            </Link>
          </li>
        ))}
      </ul>
    </article>
  );
}
