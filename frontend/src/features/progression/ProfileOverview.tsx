"use client";
/**
 * Центральная и правая колонки профиля (макет v3, Profile): рейтинг, статистика, календарь
 * активности, серии, Версы; задания, бейджи, колесо, архив событий, переходы. Каждый блок — плитка;
 * блоки bff гаснут независимо (`unavailable`) — экран при этом цел. Названия заданий, бейджей и
 * секторов приходят из progression и показываются как есть.
 */
import type { ReactNode } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type {
  BadgeCard,
  ProfilePage,
  ProgressCard,
  QuestCard,
  QuestsCard,
  RatingCard,
  WalletCard,
} from "@/shared/api/types";
import Link from "next/link";
import { cn } from "@/shared/lib/cn";
import { StatTile } from "@/shared/ui/stat-tile";
import { Tile } from "@/shared/ui/tile";
import { RatingPanel } from "@/features/rating/RatingPanel";
import type { SeasonCard } from "@/shared/api/types";
import { useNow } from "@/features/daily/use-now";
import { useTierLabel } from "@/features/rating/labels";
import { EventsArchive } from "@/features/events/EventsArchive";
import { ActivityCalendar } from "./ActivityCalendar";
import { ProfileLinks } from "./ProfileLinks";
import { WheelBlock } from "./WheelBlock";

type Block<T> = { status: "ok" | "unavailable"; data?: T | null } | undefined;

type ColumnProps = {
  blocks: ProfilePage["blocks"];
  ratings: RatingCard[] | null;
  seasons: SeasonCard[] | null;
  playedToday: boolean;
};

function streakOf(progress: ProgressCard | null | undefined): number {
  return Math.max(0, ...(progress?.streaks.map((s) => s.current) ?? []));
}

/** Центр: рейтинг, статистика, календарь активности, серии дейлика, Версы. */
export function ProfileCenter({ blocks, ratings, seasons, playedToday }: ColumnProps) {
  const t = useTranslations();
  const progress = blocks.progress?.status === "ok" ? blocks.progress.data : null;
  const quests = blocks.quests?.status === "ok" ? blocks.quests.data : null;
  const streak = streakOf(progress);
  const now = useNow();
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <Tile
        cap={t("progression.cols.rating")}
        aside={
          <Link
            href="/leaderboards"
            data-testid="nav-leaderboards"
            className="text-xs font-semibold text-music"
          >
            {t("progression.cols.leaders")}
          </Link>
        }
        data-testid="profile-rating"
      >
        <RatingPanel ratings={ratings} seasons={seasons} />
      </Tile>
      <Tile cap={t("progression.cols.stats")}>
        <Tiles
          progress={progress ?? null}
          quests={quests ?? null}
          ratings={ratings}
          streak={streak}
        />
      </Tile>
      {now && <ActivityCalendar today={now} streak={streak} playedToday={playedToday} />}
      <Section id="level" title={t("progression.streaks.title")} block={blocks.progress}>
        {(p: ProgressCard) => <LevelDetails progress={p} />}
      </Section>
      <Section id="wallet" title={t("progression.wallet.title")} block={blocks.wallet}>
        {(w: WalletCard) => <WalletBlock wallet={w} />}
      </Section>
    </div>
  );
}

/** Правая колонка: задания, бейджи, колесо, архив событий, переходы в разделы. */
export function ProfileRight({ blocks }: { blocks: ProfilePage["blocks"] }) {
  const t = useTranslations();
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <Section id="quests" title={t("progression.quests.title")} block={blocks.quests}>
        {(q: QuestsCard) => <QuestsBlock quests={q} />}
      </Section>
      <Section id="badges" title={t("progression.badges.title")} block={blocks.badges}>
        {(b: BadgeCard[]) => <BadgesBlock badges={b} />}
      </Section>
      <Section id="wheel" title={t("progression.wheel.title")} block={blocks.wheel}>
        {(w) => <WheelBlock wheel={w} />}
      </Section>
      <EventsArchive events={blocks.events ?? []} />
      <Tile cap={t("progression.links.label")}>
        <ProfileLinks />
      </Tile>
    </div>
  );
}

function Tiles({
  progress,
  quests,
  ratings,
  streak,
}: {
  progress: ProgressCard | null;
  quests: QuestsCard | null;
  ratings: RatingCard[] | null;
  streak: number;
}) {
  const t = useTranslations();
  const f = useFormatter();
  const tierLabel = useTierLabel();
  const rated = ratings?.find((r) => !r.calibrating && r.tier && r.rating != null) ?? null;
  const daily = quests?.daily ?? [];
  const none = t("progression.tiles.none");
  return (
    <section
      aria-label={t("progression.cols.stats")}
      className="grid grid-cols-2 gap-2"
      data-testid="profile-tiles"
    >
      <StatTile accent value={streak} label={t("progression.tiles.streak")} />
      <StatTile
        value={rated ? f.number(rated.rating!) : none}
        label={
          rated
            ? t("progression.tiles.rating", { tier: tierLabel(rated.tier!) })
            : t("progression.tiles.ratingNone")
        }
      />
      <StatTile
        value={progress ? f.number(progress.total_xp) : none}
        label={t("progression.tiles.xp")}
      />
      <StatTile
        value={quests ? `${daily.filter((q) => q.completed).length}/${daily.length}` : none}
        label={t("progression.tiles.quests")}
      />
    </section>
  );
}

function Section<T>({
  id,
  title,
  block,
  children,
}: {
  id: string;
  title: string;
  block: Block<T>;
  children: (data: T) => ReactNode;
}) {
  const t = useTranslations();
  const data = block?.status === "ok" ? block.data : null;
  return (
    <Tile cap={title} data-testid={`block-${id}`}>
      {data ? (
        children(data)
      ) : (
        <p className="text-sm text-ink-muted" data-testid={`block-${id}-unavailable`}>
          {t("progression.blockUnavailable")}
        </p>
      )}
    </Tile>
  );
}

const ROW = "flex flex-col gap-1 rounded-xl bg-stage-raised px-3.5 py-3";

function LevelDetails({ progress }: { progress: ProgressCard }) {
  const t = useTranslations();
  const f = useFormatter();
  return (
    <>
      {progress.streaks.length ? (
        <ul className="flex flex-col gap-2.5" data-testid="streaks">
          {progress.streaks.map((s) => (
            <li
              key={s.domain}
              className="flex min-h-10 items-center justify-between gap-3 rounded-xl bg-stage-raised px-3.5"
            >
              <span>{t(s.domain === "music" ? "domain.music" : "domain.film")}</span>
              <span className="tabular text-sm text-ink-muted">
                {t("progression.streaks.value", { current: s.current, best: s.best })}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">{t("progression.streaks.empty")}</p>
      )}
      {progress.boost && (
        <p className={cn(ROW, "text-sm")} data-testid="boost">
          {t("progression.boost", {
            multiplier: f.number(progress.boost.multiplier, { maximumFractionDigits: 2 }),
            matches: progress.boost.matches_left,
          })}
        </p>
      )}
      <p className="tabular px-0.5 text-sm text-ink-muted" data-testid="solo-cap">
        {t("progression.soloCap", {
          today: f.number(progress.solo_xp_today),
          cap: f.number(progress.solo_xp_cap),
        })}
      </p>
    </>
  );
}

function WalletBlock({ wallet }: { wallet: WalletCard }) {
  const t = useTranslations();
  const f = useFormatter();
  return (
    <div className={ROW}>
      <p className="font-display text-2xl font-extrabold" data-testid="wallet-total">
        {t("progression.wallet.total", { total: f.number(wallet.total) })}
      </p>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-ink-muted">{t("progression.wallet.earned")}</dt>
          <dd className="tabular" data-testid="wallet-earned">
            {f.number(wallet.earned)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-muted">{t("progression.wallet.paid")}</dt>
          <dd className="tabular" data-testid="wallet-paid">
            {f.number(wallet.paid)}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function QuestsBlock({ quests }: { quests: QuestsCard }) {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-4">
      <QuestList title={t("progression.quests.daily")} id="daily" items={quests.daily} />
      <QuestList title={t("progression.quests.weekly")} id="weekly" items={quests.weekly} />
    </div>
  );
}

function QuestList({ title, id, items }: { title: string; id: string; items: QuestCard[] }) {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-2.5">
      <h3 className="px-0.5 text-sm text-ink-muted">{title}</h3>
      {items.length ? (
        <ul className="flex flex-col gap-2.5" data-testid={`quests-${id}`}>
          {items.map((q) => (
            <QuestRow key={q.key} quest={q} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-ink-muted">{t("progression.quests.empty")}</p>
      )}
    </div>
  );
}

function QuestRow({ quest }: { quest: QuestCard }) {
  const t = useTranslations();
  const done = Math.min(quest.progress, quest.target);
  const percent = Math.floor((done / quest.target) * 100);
  return (
    <li data-testid={`quest-${quest.key}`} data-completed={quest.completed} className={ROW}>
      <div className="flex items-baseline justify-between gap-3">
        <span dir="auto" className="font-medium">
          {quest.title}
        </span>
        {quest.completed && (
          <span className="text-xs font-medium text-exact">{t("progression.quests.done")}</span>
        )}
      </div>
      {quest.description && (
        <p dir="auto" className="text-sm text-ink-muted">
          {quest.description}
        </p>
      )}
      <div
        role="progressbar"
        aria-label={quest.title}
        aria-valuemin={0}
        aria-valuemax={quest.target}
        aria-valuenow={done}
        aria-valuetext={t("progression.quests.progress", { done, target: quest.target })}
        className="h-2 overflow-hidden rounded bg-stage-raised"
      >
        <div className="h-full bg-music" style={{ width: `${percent}%` }} />
      </div>
      <p className="tabular flex justify-between text-xs text-ink-muted">
        <span>{t("progression.quests.progress", { done, target: quest.target })}</span>
        <span>
          {t("progression.quests.reward", { xp: quest.reward_xp, verses: quest.reward_verses })}
        </span>
      </p>
    </li>
  );
}

function BadgesBlock({ badges }: { badges: BadgeCard[] }) {
  const t = useTranslations();
  if (!badges.length) return <p className="text-ink-muted">{t("progression.badges.empty")}</p>;
  return (
    <ul className="flex flex-col gap-2.5" data-testid="badges">
      {badges.map((b) => {
        const unlocked = b.unlocked_at !== null && b.unlocked_at !== undefined;
        return (
          <li
            key={b.key}
            data-testid={`badge-${b.key}`}
            data-unlocked={unlocked}
            className={cn(ROW, !unlocked && "opacity-80")}
          >
            <span dir="auto" className="font-medium">
              {b.title}
            </span>
            <span dir="auto" className="text-sm text-ink-muted">
              {b.description}
            </span>
            <span className="tabular text-xs text-ink-muted">
              {unlocked
                ? t("progression.badges.unlocked")
                : t("progression.badges.progress", {
                    progress: Math.min(b.progress, b.target),
                    target: b.target,
                  })}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
