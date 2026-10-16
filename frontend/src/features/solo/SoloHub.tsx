"use client";
/**
 * Соло-хаб по блокам bff (`GET /v1/pages/solo`): наборы дня, сетка недели, витрина режимов
 * группами (`source_type` + `presentation`, порядок — как прислал bff) и личные рекорды.
 *
 * Карточка режима строится по данным: пресеты — чипы старта, `start.subject` — переход к выбору
 * персоны на `/play/<key>`. Ключ режима нужен только для ссылки и `data-testid`.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { Trophy } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import type { ModeCard, ModeGroup, RecordCard, SoloPage } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { DailyTicket } from "@/features/daily/DailyCards";

const descriptionKey = (titleKey: string) => titleKey.replace(/\.title$/, ".description");

export function SoloHub({ initial }: { initial: SoloPage | null }) {
  const t = useTranslations();
  const { data } = useQuery({
    queryKey: ["page", "solo"],
    queryFn: async (): Promise<SoloPage> => unwrap(await api().web.GET("/v1/pages/solo")),
    initialData: initial ?? undefined,
  });
  const daily = data?.blocks.daily;
  const weekly = data?.blocks.weekly;
  const modes = data?.blocks.modes;
  const records = data?.blocks.records ?? null;
  const dailies = [
    ...(daily?.status === "ok" ? (daily.data ?? []) : []),
    ...(weekly?.status === "ok" ? (weekly.data ?? []) : []),
  ];
  const dailyUnavailable = daily?.status === "unavailable" && weekly?.status !== "ok";
  return (
    <div className="flex flex-col gap-12">
      <section aria-labelledby="solo-daily" className="flex flex-col gap-5">
        <h2 id="solo-daily" className="font-display text-xl font-semibold">
          {t("daily.title")}
        </h2>
        {!data || dailyUnavailable ? (
          <p className="text-ink-muted">{t("daily.unavailable")}</p>
        ) : (
          <ul className="grid gap-5 md:grid-cols-2">
            {dailies.map((card) => (
              <li key={card.mode_key}>
                <DailyTicket card={card} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {records?.status === "ok" && (records.data ?? []).length > 0 && (
        <Records records={records.data ?? []} />
      )}

      {modes?.status === "ok" ? (
        (modes.data ?? []).map((group) => (
          <Group key={`${group.source_type}:${group.presentation}`} group={group} />
        ))
      ) : (
        <p className="text-ink-muted">{t("solo.modesUnavailable")}</p>
      )}
    </div>
  );
}

function Group({ group }: { group: ModeGroup }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const id = `solo-group-${group.source_type}-${group.presentation}`;
  const fallback = t(`solo.presentation.${group.presentation}`);
  return (
    <section
      aria-labelledby={id}
      className="flex flex-col gap-5"
      data-testid={`mode-group-${group.source_type}`}
    >
      <div>
        <h2 id={id} className="font-display text-xl font-semibold">
          {dt(`solo.group.${group.source_type}.title`, fallback)}
        </h2>
        <p className="mt-1 text-ink-muted">
          {dt(`solo.group.${group.source_type}.text`, t("solo.groupText"))}
        </p>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        {group.modes.map((mode) => (
          <ModeCardView key={mode.key} mode={mode} />
        ))}
      </div>
    </section>
  );
}

function ModeCardView({ mode }: { mode: ModeCard }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const chip =
    "inline-flex h-10 items-center rounded-full px-4 text-sm font-medium ring-1 ring-inset transition-colors";
  const subject = mode.start?.subject ?? null;
  const presets = mode.presets ?? [];
  // Число раундов фиксировано только у режимов без «жизней» и выбора: иначе `rounds` — предел.
  const fixedRounds = (mode.presentation ?? "score") === "score" && !subject;
  const description = dt(descriptionKey(mode.title_key), "");
  return (
    <article
      data-domain={mode.domain}
      data-testid={`mode-card-${mode.key}`}
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule"
    >
      <div>
        <p className="text-sm text-[var(--accent)]">{t(`domain.${mode.domain}`)}</p>
        <h3 className="mt-1 font-display text-xl font-bold">{dt(mode.title_key, mode.key)}</h3>
        {description && <p className="mt-1 text-ink-muted">{description}</p>}
        {fixedRounds && (
          <p className="mt-1 text-sm text-ink-muted">{t("solo.rounds", { count: mode.rounds })}</p>
        )}
      </div>
      <ul className="mt-auto flex flex-wrap gap-2" aria-label={t("solo.presets")}>
        <li>
          <Link
            href={`/play/${mode.key}`}
            className={`${chip} bg-[var(--accent)] text-night ring-transparent hover:brightness-110`}
          >
            {subject
              ? t("solo.pickSubject")
              : presets.length
                ? t("solo.anyPreset")
                : t("solo.play")}
          </Link>
        </li>
        {presets.map((p) => (
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

function Records({ records }: { records: RecordCard[] }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const format = useFormatter();
  return (
    <section aria-labelledby="solo-records" className="flex flex-col gap-5">
      <h2 id="solo-records" className="font-display text-xl font-semibold">
        {t("records.title")}
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {records.map((r) => (
          <li
            key={r.mode_key}
            data-domain={r.domain}
            data-testid={`record-${r.mode_key}`}
            className="flex items-center gap-4 rounded-xl bg-stage px-4 py-3 ring-1 ring-rule"
          >
            <Trophy aria-hidden className="size-5 shrink-0 text-[var(--accent)]" />
            <div className="min-w-0">
              <p className="truncate font-medium">{dt(r.title_key, r.mode_key)}</p>
              <p className="text-sm text-ink-muted">
                {t(`records.best.${r.presentation}`, { best: r.best })}
                {r.percentile !== null && r.percentile !== undefined
                  ? ` · ${t("records.percentile", { pct: Math.round(r.percentile) })}`
                  : ""}
              </p>
              <p className="text-xs text-ink-muted">
                {format.dateTime(new Date(r.achieved_at), { day: "numeric", month: "long" })}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
