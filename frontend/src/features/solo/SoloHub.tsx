"use client";
/**
 * Соло-хаб по макету v3 (Solo) и блокам bff (`GET /v1/pages/solo`): переключатель домена и фильтр
 * видов режимов, три плитки (дейлик, сетка недели, сборник), сетка быстрых режимов, плитки
 * марафонов и пар. Режимы группируются по данным: группа с `presentation: "score"` — карточки
 * сетки (раскрываются по клику: описание, пресеты, «Играть»), остальные — плитка со строками.
 * Ключи режимов нужны только для ссылок и `data-testid`.
 */
import Link from "next/link";
import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { blockData } from "@/shared/api/blocks";
import type {
  DailyCard,
  Domain,
  ModeCard,
  ModeGroup,
  RecordCard,
  SoloPage,
} from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { collectionKeys, fetchShowcase } from "@/features/collections/api";
import { dailyHref } from "@/features/home/DailyTiles";
import { formatClock, remainingUntil } from "@/features/daily/countdown-time";
import { useNow } from "@/features/daily/use-now";
import { Chip } from "@/shared/ui/chip";
import { Cover } from "@/shared/ui/cover";
import { ListRow } from "@/shared/ui/list-row";
import { PillTabs } from "@/shared/ui/pill-tabs";
import { Segmented } from "@/shared/ui/segmented";
import { useIcons } from "@/shared/ui/theme-provider";
import { Tile } from "@/shared/ui/tile";

const ALL = "all";
/** Виды режимов, которые макет показывает плитками со строками, а не карточками сетки. */
const ROW_SOURCES = new Set(["marathon", "pair"]);
const asRows = (g: ModeGroup) => g.presentation !== "score" || ROW_SOURCES.has(g.source_type);

const CHIP = "rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted";
const descriptionKey = (titleKey: string) => titleKey.replace(/\.title$/, ".description");
const groupId = (g: ModeGroup) => `${g.source_type}:${g.presentation}`;

export function SoloHub({ initial }: { initial: SoloPage | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const panelId = useId();
  const viewer = useViewer();
  const { data } = useQuery({
    queryKey: ["page", "solo"],
    queryFn: async (): Promise<SoloPage> => unwrap(await api().web.GET("/v1/pages/solo")),
    initialData: initial ?? undefined,
  });
  const [domain, setDomain] = useState<Domain>("music");
  const [filter, setFilter] = useState<string>(ALL);
  const [open, setOpen] = useState<string | null>(null);

  const blocks = data?.blocks;
  const records = new Map<string, RecordCard>(
    (blockData(blocks?.records) ?? []).map((r) => [r.mode_key, r]),
  );
  const modesUnavailable = blocks?.modes?.status !== "ok";
  const groups = (blockData(blocks?.modes) ?? [])
    .map((g) => ({ ...g, modes: g.modes.filter((m) => m.domain === domain) }))
    .filter((g) => g.modes.length > 0);
  const daily = (blockData(blocks?.daily) ?? []).find((c) => c.domain === domain);
  const weekly = (blockData(blocks?.weekly) ?? []).find((c) => c.domain === domain);

  const tabs = [
    { value: ALL, label: t("solo.tab.all") },
    ...groups.map((g) => ({
      value: groupId(g),
      label: dt(
        `solo.tab.${g.source_type}`,
        dt(`solo.group.${g.source_type}.title`, g.source_type),
      ),
    })),
  ];
  const active = tabs.some((x) => x.value === filter) ? filter : ALL;
  const shown = groups.filter((g) => active === ALL || groupId(g) === active);
  const gridModes = shown.filter((g) => !asRows(g)).flatMap((g) => g.modes);
  const streakGroups = shown.filter(asRows);

  function pickDomain(next: Domain) {
    setDomain(next);
    setFilter(ALL);
    setOpen(null);
  }

  return (
    <div className="flex flex-col gap-5" data-testid="solo">
      <h1 className="sr-only">{t("solo.title")}</h1>
      <section className="flex flex-wrap items-center justify-between gap-4">
        <Segmented
          aria-label={t("solo.domainLabel")}
          value={domain}
          onChange={pickDomain}
          options={[
            { value: "music", label: t("domain.music") },
            { value: "film", label: t("domain.film") },
          ]}
        />
        <PillTabs
          aria-label={t("solo.tabsLabel")}
          tabs={tabs}
          value={active}
          onChange={(v) => {
            setFilter(v);
            setOpen(null);
          }}
          panelId={panelId}
        />
      </section>

      <section className="grid items-stretch gap-5 md:grid-cols-2 lg:grid-cols-3">
        <DailyTile domain={domain} card={daily} />
        {weekly && <BracketTile card={weekly} />}
        <WeekCollectionTile domain={domain} />
      </section>

      <section id={panelId} role="tabpanel" className="flex flex-col gap-5">
        {modesUnavailable && <p className="text-ink-muted">{t("solo.modesUnavailable")}</p>}
        {gridModes.length > 0 && (
          <div className="flex flex-col gap-2.5" data-testid="mode-group-score">
            <span className="text-[11px] font-semibold tracking-[0.12em] text-ink-faint uppercase">
              {t("solo.modes")}
            </span>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {gridModes.map((mode) => (
                <ModeCardTile
                  key={mode.key}
                  mode={mode}
                  record={records.get(mode.key)}
                  isUser={viewer.kind === "user"}
                  open={open === mode.key}
                  onToggle={() => setOpen(open === mode.key ? null : mode.key)}
                />
              ))}
            </div>
          </div>
        )}
        {streakGroups.length > 0 && (
          <div className="grid gap-5 md:grid-cols-2" data-testid="mode-group-streak">
            {streakGroups.map((g) => (
              <StreakTile key={groupId(g)} group={g} records={records} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ResetChip({ target }: { target: string }) {
  const t = useTranslations();
  const now = useNow();
  const clock = now ? formatClock(remainingUntil(new Date(target), now)) : "––:––:––";
  return (
    <span className={cn(CHIP, "tabular relative whitespace-nowrap")}>
      {t("home.daily.resetIn", { clock })}
    </span>
  );
}

/** Дейлик выбранного домена: единственная янтарная кнопка экрана — круглый Play. */
function DailyTile({ domain, card }: { domain: Domain; card: DailyCard | undefined }) {
  const t = useTranslations();
  const icons = useIcons();
  const Play = icons.play;
  const Calendar = icons.calendar;
  const Glyph = icons[domain];
  const finished = card?.status === "finished";
  return (
    <Tile
      accent
      cap={t(`home.daily.${domain}.cap`)}
      aside={card ? <ResetChip target={card.next_reset_at} /> : undefined}
      title={t(`solo.daily.${domain}.title`)}
      subtitle={t(`solo.daily.${domain}.sub`)}
      data-testid={`solo-daily-${domain}`}
    >
      <Glyph
        aria-hidden
        className="pointer-events-none absolute top-9 -right-2.5 size-24 text-stage-raised"
        strokeWidth={1.5}
      />
      <div className="relative mt-auto flex items-center gap-2.5">
        <ListRow className="flex-1">
          <span className="flex items-center gap-2">
            <Calendar aria-hidden className="size-4 text-ink-muted" />
            {card
              ? t(`home.daily.status.${card.status}`, {
                  score: card.score ?? 0,
                  max: card.max_score,
                })
              : t("home.daily.unavailable")}
          </span>
        </ListRow>
        {card && (
          <Link
            href={dailyHref(card)}
            aria-label={finished ? t("daily.action.finished") : t("solo.play")}
            data-testid={`solo-daily-play-${domain}`}
            className="grid size-11 flex-none place-items-center rounded-full bg-music text-night"
          >
            <Play aria-hidden className="ml-0.5 size-5" fill="currentColor" />
          </Link>
        )}
      </div>
    </Tile>
  );
}

/** Сетка недели («Топ-32»): плитка только если бэкенд отдал набор недели для домена. */
function BracketTile({ card }: { card: DailyCard }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const icons = useIcons();
  const Glyph = icons[card.domain];
  return (
    <Tile
      cap={t("solo.weekly.cap", { domain: t(`domain.${card.domain}`).toLowerCase() })}
      aside={<ResetChip target={card.next_reset_at} />}
      data-testid={`daily-${card.mode_key}`}
    >
      <div className="flex items-start gap-3.5">
        <Cover
          ratio="poster"
          className="w-[76px] flex-none"
          placeholder={<Glyph className="size-6" />}
        />
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="font-display text-xl leading-[1.1] font-extrabold tracking-[-0.02em]">
            {dt(card.title_key, card.mode_key)}
          </span>
          <span className="text-[13px] leading-[1.45] text-ink-muted">
            {dt(descriptionKey(card.title_key), t(`daily.pitch.${card.domain}`))}
          </span>
          <span className="w-fit rounded-full bg-glow px-[9px] py-[3px] text-xs font-semibold text-music">
            {t(`daily.statusPlain.${card.status}`)}
          </span>
        </div>
      </div>
      <Link
        href={dailyHref(card)}
        className="mt-auto grid h-[34px] place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised text-[13px] font-semibold hover:border-rule-strong"
      >
        {t(`daily.action.${card.status}`)}
      </Link>
    </Tile>
  );
}

/** «Сборник недели»: первый избранный сборник домена; нет данных — вход в каталог. */
function WeekCollectionTile({ domain }: { domain: Domain }) {
  const t = useTranslations();
  const icons = useIcons();
  const showcase = useQuery({
    queryKey: collectionKeys.showcase({ domain }),
    queryFn: () => fetchShowcase({ domain }),
    staleTime: 60_000,
  });
  const blocks = showcase.data?.blocks;
  const card = blockData(blocks?.featured)?.items[0] ?? blockData(blocks?.popular)?.items[0];
  const Glyph = icons[domain];
  if (!card)
    return (
      <Tile
        href="/collections"
        cap={t("solo.collection.cap")}
        title={t("solo.collection.emptyTitle")}
        subtitle={t("solo.collection.emptySub")}
        data-testid="solo-collection-week"
      >
        <span className="mt-auto grid h-[34px] place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised text-[13px] font-semibold">
          {t("solo.collection.open")}
        </span>
      </Tile>
    );
  return (
    <Tile
      href={`/collections/${card.collection_id}`}
      cap={t("solo.collection.cap")}
      aside={<span className={CHIP}>{t(`domain.${card.domain}`)}</span>}
      data-testid="solo-collection-week"
    >
      <div className="flex items-start gap-3.5">
        <Cover className="w-24 flex-none" placeholder={<Glyph className="size-6" />} />
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="font-display text-xl leading-[1.1] font-extrabold tracking-[-0.02em]">
            {card.title}
          </span>
          <span className="text-[13px] leading-[1.45] text-ink-muted">
            {t("solo.collection.meta", { count: card.items_count, likes: card.likes_count })}
          </span>
          {card.author.nickname && (
            <span className="text-xs text-ink-faint">
              {t("solo.collection.author", { name: card.author.nickname })}
            </span>
          )}
        </div>
      </div>
      <span className="mt-auto grid h-[34px] place-items-center rounded-full bg-ink text-[13px] font-semibold text-night">
        {t("solo.play")}
      </span>
    </Tile>
  );
}

/** Карточка режима в сетке: заголовок-кнопка раскрывает описание, пресеты и «Играть». */
function ModeCardTile({
  mode,
  record,
  isUser,
  open,
  onToggle,
}: {
  mode: ModeCard;
  record: RecordCard | undefined;
  isUser: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const icons = useIcons();
  const bodyId = useId();
  const [preset, setPreset] = useState<string | null>(null);
  const subject = mode.start?.subject ?? null;
  const presets = mode.presets ?? [];
  const Glyph = icons[mode.domain];
  const Chevron = icons.expand;
  const Play = icons.play;
  const description = dt(descriptionKey(mode.title_key), "");
  // У гостя рекордов нет; у игрока без рекорда — «ещё не играл».
  const rec = record
    ? { text: t(`records.best.${record.presentation}`, { best: record.best }), good: true }
    : isUser
      ? { text: t("solo.notPlayed"), good: false }
      : null;
  const href = preset
    ? `/play/${mode.key}?preset=${encodeURIComponent(preset)}`
    : `/play/${mode.key}`;
  return (
    <Tile
      data-testid={`mode-card-${mode.key}`}
      className={cn("gap-2 rounded-2xl px-3.5 py-3", open && "sm:col-span-2 lg:col-span-4")}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={onToggle}
        className="flex w-full items-center gap-2.5 text-left"
      >
        <span className="grid size-[34px] flex-none place-items-center rounded-[10px] bg-stage-raised text-music">
          <Glyph aria-hidden size={18} />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="text-sm leading-tight font-semibold">
            {dt(mode.title_key, mode.key)}
          </span>
          {description && (
            <span className="truncate text-xs leading-tight text-ink-muted">{description}</span>
          )}
        </span>
        <Chevron
          aria-hidden
          size={16}
          className={cn("flex-none text-ink-muted transition-transform", open && "rotate-180")}
        />
      </button>
      {rec && (
        <span className={cn("text-[11px]", rec.good ? "text-exact" : "text-ink-faint")}>
          {rec.text}
        </span>
      )}
      {open && (
        <div id={bodyId} className="flex animate-rise flex-col gap-3 pt-1">
          {description && <p className="text-[15px] leading-normal text-ink-soft">{description}</p>}
          {!subject && mode.presentation === "score" && (
            <p className="text-[13px] text-ink-muted">{t("solo.rounds", { count: mode.rounds })}</p>
          )}
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
            <Link
              href={href}
              className="inline-flex h-11 items-center gap-2 rounded-[14px] border-[1.5px] border-rule bg-stage-raised px-[18px] text-[15px] font-semibold hover:border-rule-strong"
            >
              {!subject && <Play aria-hidden size={16} />}
              {subject ? t("solo.pickSubject") : t("solo.play")}
            </Link>
          </div>
        </div>
      )}
    </Tile>
  );
}

/** Плитка группы со строками (марафоны, пары): рекорд слева, режим, переход в игру справа. */
function StreakTile({ group, records }: { group: ModeGroup; records: Map<string, RecordCard> }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const icons = useIcons();
  const Next = icons.next;
  const domains = new Set(group.modes.map((m) => m.domain));
  const label = dt(
    `solo.tab.${group.source_type}`,
    dt(`solo.group.${group.source_type}.title`, group.source_type),
  );
  return (
    <Tile
      cap={label}
      aside={domains.size === 1 && <span className={CHIP}>{t(`domain.${[...domains][0]!}`)}</span>}
      data-testid={`mode-group-${group.source_type}`}
    >
      <div className="flex flex-col gap-1.5">
        {group.modes.map((mode) => {
          const record = records.get(mode.key);
          const subject = mode.start?.subject ?? null;
          return (
            <div key={mode.key} data-testid={`mode-card-${mode.key}`}>
              <ListRow
                lead={record?.best ?? "–"}
                accentLead
                trailing={
                  <Link
                    href={`/play/${mode.key}`}
                    className="inline-flex items-center gap-1 font-semibold text-ink hover:text-music"
                  >
                    {subject ? t("solo.pickSubject") : t("solo.play")}
                    <Next aria-hidden className="size-3.5" />
                  </Link>
                }
              >
                {dt(mode.title_key, mode.key)}
              </ListRow>
            </div>
          );
        })}
      </div>
      <span className="text-xs text-ink-muted">{t(`solo.sub.${group.presentation}`)}</span>
    </Tile>
  );
}
