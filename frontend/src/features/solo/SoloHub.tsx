"use client";
/**
 * Соло-хаб по блокам bff (`GET /v1/pages/solo`): домен переключателем, вкладки по группам режимов
 * (`source_type`, порядок — как прислал bff), в каждой — строки режимов, которые раскрываются
 * по клику: описание, наборы-пресеты и главная кнопка «Играть». Личный рекорд — в самой строке.
 * Сетка недели (плановый режим) — отдельная вкладка; сборники — вкладка со ссылками.
 *
 * Строка строится по данным: пресеты — чипы выбора набора, `start.subject` — переход к выбору
 * персоны на `/play/<key>`. Ключ режима нужен только для ссылки и `data-testid`.
 */
import Link from "next/link";
import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import type { DailyCard, ModeCard, ModeGroup, RecordCard, SoloPage } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { Chip } from "@/shared/ui/chip";
import { Segmented } from "@/shared/ui/segmented";
import { useIcons } from "@/shared/ui/theme-provider";
import { HubHeader } from "@/features/hubs/HubHeader";
import { PillTabs } from "@/shared/ui/pill-tabs";

type Domain = "music" | "film";

const WEEKLY_TAB = "weekly";
const COLLECTION_TAB = "collection";

const descriptionKey = (titleKey: string) => titleKey.replace(/\.title$/, ".description");
const groupId = (g: ModeGroup) => `${g.source_type}:${g.presentation}`;

export function SoloHub({ initial }: { initial: SoloPage | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const panelId = useId();
  const { data } = useQuery({
    queryKey: ["page", "solo"],
    queryFn: async (): Promise<SoloPage> => unwrap(await api().web.GET("/v1/pages/solo")),
    initialData: initial ?? undefined,
  });
  const [domain, setDomain] = useState<Domain>("music");
  const [tab, setTab] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const weekly = data?.blocks.weekly;
  const modes = data?.blocks.modes;
  const records = new Map<string, RecordCard>(
    (data?.blocks.records?.status === "ok" ? (data.blocks.records.data ?? []) : []).map((r) => [
      r.mode_key,
      r,
    ]),
  );
  const groups = (modes?.status === "ok" ? (modes.data ?? []) : [])
    .map((g) => ({ ...g, modes: g.modes.filter((m) => m.domain === domain) }))
    .filter((g) => g.modes.length > 0);
  const weeklyCards = (weekly?.status === "ok" ? (weekly.data ?? []) : []).filter(
    (c) => c.domain === domain,
  );

  const tabs = [
    ...groups.map((g) => ({
      value: groupId(g),
      label: dt(
        `solo.tab.${g.source_type}`,
        dt(`solo.group.${g.source_type}.title`, g.source_type),
      ),
    })),
    ...(weeklyCards.length ? [{ value: WEEKLY_TAB, label: t("solo.tab.weekly") }] : []),
    { value: COLLECTION_TAB, label: t("solo.tab.collection") },
  ];
  const active = tabs.some((x) => x.value === tab) ? tab! : tabs[0]!.value;
  const activeGroup = groups.find((g) => groupId(g) === active);

  function pickTab(value: string) {
    setTab(value);
    // Как в макете: при смене вкладки раскрыта первая строка.
    const g = groups.find((x) => groupId(x) === value);
    setOpen(g?.modes[0]?.key ?? (value === WEEKLY_TAB ? (weeklyCards[0]?.mode_key ?? null) : null));
  }

  function pickDomain(next: Domain) {
    setDomain(next);
    setTab(null);
    setOpen(null);
  }

  // Раскрытая строка: явный выбор, а пока его нет — первая строка первой вкладки.
  const firstKey =
    activeGroup?.modes[0]?.key ?? (active === WEEKLY_TAB ? weeklyCards[0]?.mode_key : null);
  const openKey = open ?? (tab === null ? (firstKey ?? null) : null);
  const toggle = (key: string) => setOpen(openKey === key ? "" : key);

  return (
    <div className="flex flex-col gap-5 pt-4">
      <div className="flex justify-center">
        <Segmented
          aria-label={t("solo.domainLabel")}
          value={domain}
          onChange={pickDomain}
          options={[
            { value: "music", label: t("domain.music") },
            { value: "film", label: t("domain.film") },
          ]}
        />
      </div>
      <HubHeader title={t("solo.title")} aside={t("solo.aside")} />
      <PillTabs
        aria-label={t("solo.tabsLabel")}
        tabs={tabs}
        value={active}
        onChange={pickTab}
        panelId={panelId}
      />
      <div
        id={panelId}
        role="tabpanel"
        data-testid={activeGroup ? `mode-group-${activeGroup.source_type}` : `mode-group-${active}`}
        className="flex flex-col gap-2.5"
      >
        {modes?.status !== "ok" && active !== COLLECTION_TAB && active !== WEEKLY_TAB && (
          <p className="text-ink-muted">{t("solo.modesUnavailable")}</p>
        )}
        {activeGroup?.modes.map((mode) => (
          <ModeRow
            key={mode.key}
            mode={mode}
            record={records.get(mode.key)}
            open={openKey === mode.key}
            onToggle={() => toggle(mode.key)}
          />
        ))}
        {active === WEEKLY_TAB &&
          weeklyCards.map((card) => (
            <WeeklyRow
              key={card.mode_key}
              card={card}
              open={openKey === card.mode_key}
              onToggle={() => toggle(card.mode_key)}
            />
          ))}
        {active === COLLECTION_TAB && <CollectionLinks />}
      </div>
    </div>
  );
}

/** Общая оболочка раскрывающейся строки: заголовок-кнопка и панель под ней. */
function ExpandRow({
  testId,
  domain,
  title,
  sub,
  trailing,
  open,
  onToggle,
  children,
}: {
  testId: string;
  domain: Domain;
  title: string;
  sub: string;
  trailing?: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const icons = useIcons();
  const panelId = useId();
  const Glyph = icons[domain];
  const Chevron = icons.expand;
  return (
    <div data-domain={domain} data-testid={testId}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
        className={cn(
          "flex min-h-[60px] w-full items-center gap-3.5 rounded-[var(--radius-control)] border-[1.5px] px-[18px] py-2 text-left transition-colors",
          open
            ? "rounded-b-none border-music bg-stage shadow-[0_0_0_4px_var(--color-glow)]"
            : "border-rule hover:border-rule-strong hover:bg-stage",
        )}
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-stage-raised text-music">
          <Glyph aria-hidden size={20} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-base font-semibold">{title}</span>
          <span className="text-[13px] text-ink-muted">{sub}</span>
        </span>
        {trailing && (
          <span className="text-[13px] whitespace-nowrap text-ink-muted">{trailing}</span>
        )}
        <Chevron
          aria-hidden
          size={18}
          className={cn("shrink-0 text-ink-muted transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div
          id={panelId}
          className="flex animate-rise flex-col gap-3.5 rounded-b-[var(--radius-control)] border-[1.5px] border-t-0 border-music bg-stage px-[18px] pt-4 pb-[18px]"
        >
          {children}
        </div>
      )}
    </div>
  );
}

function ModeRow({
  mode,
  record,
  open,
  onToggle,
}: {
  mode: ModeCard;
  record: RecordCard | undefined;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const icons = useIcons();
  const [preset, setPreset] = useState<string | null>(null);
  const subject = mode.start?.subject ?? null;
  const presets = mode.presets ?? [];
  const presentation = mode.presentation ?? "score";
  // Число раундов фиксировано только у режимов без «жизней» и выбора: иначе `rounds` — предел.
  const fixedRounds = presentation === "score" && !subject;
  const description = dt(descriptionKey(mode.title_key), "");
  const sub = fixedRounds
    ? t("solo.rounds", { count: mode.rounds })
    : subject
      ? t("solo.sub.subject")
      : t(`solo.sub.${presentation}`);
  const href = preset
    ? `/play/${mode.key}?preset=${encodeURIComponent(preset)}`
    : `/play/${mode.key}`;
  const Play = icons.play;
  return (
    <ExpandRow
      testId={`mode-card-${mode.key}`}
      domain={mode.domain}
      title={dt(mode.title_key, mode.key)}
      sub={sub}
      trailing={
        record ? t(`records.best.${record.presentation}`, { best: record.best }) : undefined
      }
      open={open}
      onToggle={onToggle}
    >
      {description && <p className="text-[15px] leading-normal text-ink-soft">{description}</p>}
      {record?.percentile != null && (
        <p className="text-[13px] text-ink-muted">
          {t("records.percentile", { pct: Math.round(record.percentile) })}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        {presets.length > 0 ? (
          <div
            role="group"
            aria-label={t("solo.presets")}
            className="flex flex-wrap items-center gap-2"
          >
            <Chip selected={preset === null} onClick={() => setPreset(null)}>
              {t("solo.anyPreset")}
            </Chip>
            {presets.map((p) => (
              <Chip key={p.id} selected={preset === p.id} onClick={() => setPreset(p.id)}>
                {dt(p.title_key, p.id)}
              </Chip>
            ))}
          </div>
        ) : (
          <span />
        )}
        <Button asChild>
          <Link href={href}>
            {!subject && <Play aria-hidden size={18} />}
            {subject ? t("solo.pickSubject") : t("solo.play")}
          </Link>
        </Button>
      </div>
    </ExpandRow>
  );
}

function WeeklyRow({
  card,
  open,
  onToggle,
}: {
  card: DailyCard;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const finished = card.status === "finished";
  const href =
    finished && card.session_id ? `/results/${card.session_id}` : `/play/${card.mode_key}`;
  return (
    <ExpandRow
      testId={`daily-${card.mode_key}`}
      domain={card.domain}
      title={dt(card.title_key, card.mode_key)}
      sub={t(`daily.statusPlain.${card.status}`)}
      open={open}
      onToggle={onToggle}
    >
      <p className="text-[15px] leading-normal text-ink-soft">
        {dt(descriptionKey(card.title_key), t(`daily.pitch.${card.domain}`))}
      </p>
      <div className="flex justify-end">
        <Button asChild variant={finished ? "secondary" : "primary"}>
          <Link href={href}>{t(`daily.action.${card.status}`)}</Link>
        </Button>
      </div>
    </ExpandRow>
  );
}

/** Вкладка «По сборнику»: сборники живут на своих экранах, здесь только входы. */
function CollectionLinks() {
  const t = useTranslations();
  const icons = useIcons();
  const Next = icons.next;
  return (
    <>
      {[
        {
          href: "/me/collections",
          key: "own",
          title: t("solo.collection.own.title"),
          sub: t("solo.collection.own.sub"),
        },
        {
          href: "/collections",
          key: "catalog",
          title: t("solo.collection.catalog.title"),
          sub: t("solo.collection.catalog.sub"),
        },
      ].map(({ href, key, title, sub }) => (
        <Link
          key={key}
          href={href}
          data-testid={`solo-collection-${key}`}
          className="flex min-h-[60px] w-full items-center gap-3.5 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-2 transition-colors hover:border-rule-strong hover:bg-stage"
        >
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-base font-semibold">{title}</span>
            <span className="text-[13px] text-ink-muted">{sub}</span>
          </span>
          <Next aria-hidden size={18} className="shrink-0 text-ink-muted" />
        </Link>
      ))}
    </>
  );
}
