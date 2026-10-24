"use client";
/**
 * Профиль `/profile` (этап 5a): уровень и опыт, серии дейлика, буст, Версы, задания, бейджи, колесо.
 * Только пользователю: аноним — 401 и вход, гость — 403 `auth.guest_forbidden` и регистрация.
 * Блоки bff гаснут независимо (`unavailable`) — страница при этом цела. Названия заданий, бейджей и
 * секторов приходят из progression и показываются как есть (`dir="auto"`), без перевода и хардкода.
 */
import type { ReactNode } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type {
  AppearanceCard,
  BadgeCard,
  ProfilePage,
  ProgressCard,
  QuestCard,
  QuestsCard,
  WalletCard,
} from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { blockData, hasBlock } from "@/shared/api/blocks";
import { useSessionInfo, useViewer } from "@/features/auth/session";
import { PlayerBadge, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import { fetchProfilePage, profileQueryKey } from "./api";
import { isMaxLevel } from "./logic";
import { AvatarBlock } from "./AvatarBlock";
import { PromoBlock } from "./PromoBlock";
import { WheelBlock } from "./WheelBlock";
import { XpBar } from "./XpBar";

type Block<T> = { status: "ok" | "unavailable"; data?: T | null } | undefined;

/** Кого и почему не пускаем: без входа, гость (нужен аккаунт) или отказ доступа аккаунту. */
export type GateVariant = "login" | "guest" | "forbidden";

/** 401 — вход; 403 `auth.guest_forbidden` — регистрация; любой другой 403 — нейтральный отказ. */
export function gateOf(status: number | null, code: string | null): GateVariant | null {
  if (status === 401) return "login";
  if (status === 403) return code === GUEST_FORBIDDEN ? "guest" : "forbidden";
  return null;
}

const GUEST_FORBIDDEN = "auth.guest_forbidden";

export function ProfileGate({ variant }: { variant: GateVariant }) {
  const t = useTranslations();
  if (variant === "forbidden") {
    return (
      <div className="flex max-w-xl flex-col gap-3 pt-12" data-testid="profile-gate">
        <h1 className="font-display text-2xl font-bold">{t("progression.forbidden.title")}</h1>
        <p className="text-ink-muted">{t("progression.forbidden.text")}</p>
      </div>
    );
  }
  const guest = variant === "guest";
  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12" data-testid="profile-gate">
      <h1 className="font-display text-2xl font-bold">{t("progression.usersOnly.title")}</h1>
      <p className="text-ink-muted">{t("progression.usersOnly.text")}</p>
      <Button asChild>
        <Link href={guest ? "/register" : "/login"}>
          {guest ? t("auth.signUp") : t("nav.login")}
        </Link>
      </Button>
    </div>
  );
}

export function ProfileView() {
  const t = useTranslations();
  const viewer = useViewer();
  const page = useQuery({
    queryKey: profileQueryKey,
    queryFn: fetchProfilePage,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;
  // Неудачное фоновое обновление не должно стирать уже показанный профиль.
  if (page.isError && !page.data) {
    const err = isApiError(page.error) ? page.error : null;
    const variant = gateOf(err?.status ?? null, err?.code ?? null);
    if (variant) return <ProfileGate variant={variant} />;
    return (
      <p className="pt-12 text-ink-muted" data-testid="profile-unavailable">
        {t("progression.unavailable")}
      </p>
    );
  }
  const blocks = page.data?.blocks;
  return (
    <div className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <h1 className="font-display text-3xl font-bold">{t("progression.title")}</h1>
        <p className="max-w-[60ch] text-ink-muted">{t("progression.lead")}</p>
      </header>
      {!blocks ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : (
        <ProfileBlocks blocks={blocks} />
      )}
    </div>
  );
}

function ProfileBlocks({ blocks }: { blocks: ProfilePage["blocks"] }) {
  const t = useTranslations();
  const appearance = blockData(blocks.appearance);
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <IdentityCard appearance={appearance} />
      <Section id="level" title={t("progression.level.title")} block={blocks.progress}>
        {(p: ProgressCard) => <LevelCard progress={p} />}
      </Section>
      <Section id="wallet" title={t("progression.wallet.title")} block={blocks.wallet}>
        {(w: WalletCard) => <WalletBlock wallet={w} />}
      </Section>
      <Section id="quests" title={t("progression.quests.title")} block={blocks.quests} wide>
        {(q: QuestsCard) => <QuestsBlock quests={q} />}
      </Section>
      <Section id="badges" title={t("progression.badges.title")} block={blocks.badges}>
        {(b: BadgeCard[]) => <BadgesBlock badges={b} />}
      </Section>
      <Section id="wheel" title={t("progression.wheel.title")} block={blocks.wheel}>
        {(w) => <WheelBlock wheel={w} />}
      </Section>
      <Section id="promo" title={t("promo.title")} block={{ status: "ok", data: true }}>
        {() => <PromoBlock />}
      </Section>
      {hasBlock(blocks.avatar) && (
        <Section id="avatar" title={t("avatar.title")} block={blocks.avatar ?? undefined} wide>
          {(a) => <AvatarBlock avatar={a} currentUrl={appearance?.custom_avatar_url} />}
        </Section>
      )}
    </div>
  );
}

/** Шапка профиля: ник, аватар, рамка и титул на выбранном фоне; ссылка в магазин. */
function IdentityCard({ appearance }: { appearance: AppearanceCard | null }) {
  const t = useTranslations();
  const { data } = useSessionInfo();
  const name = data?.subject?.display_name ?? t("nav.player");
  return (
    <ProfileBackdrop
      appearance={appearance}
      className="flex flex-wrap items-center justify-between gap-4 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule lg:col-span-2"
    >
      <PlayerBadge nickname={name} appearance={appearance} size="lg" />
      <Button asChild variant="secondary">
        <Link href="/shop" data-testid="to-shop">
          {t("nav.shop")}
        </Link>
      </Button>
    </ProfileBackdrop>
  );
}

function Section<T>({
  id,
  title,
  block,
  wide,
  children,
}: {
  id: string;
  title: string;
  block: Block<T>;
  wide?: boolean;
  children: (data: T) => ReactNode;
}) {
  const t = useTranslations();
  const data = block?.status === "ok" ? block.data : null;
  return (
    <section
      aria-labelledby={`${id}-title`}
      data-testid={`block-${id}`}
      className={cn(
        "flex flex-col gap-4 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule",
        wide && "lg:col-span-2",
      )}
    >
      <h2 id={`${id}-title`} className="font-display text-xl font-semibold">
        {title}
      </h2>
      {data ? (
        children(data)
      ) : (
        <p className="text-ink-muted" data-testid={`block-${id}-unavailable`}>
          {t("progression.blockUnavailable")}
        </p>
      )}
    </section>
  );
}

function LevelCard({ progress }: { progress: ProgressCard }) {
  const t = useTranslations();
  const f = useFormatter();
  return (
    <div className="flex flex-col gap-4">
      <p className="font-display text-4xl font-bold" data-testid="level-value">
        {t("progression.level.value", { level: progress.level })}
        {isMaxLevel(progress) && (
          <span className="ml-3 text-base font-medium text-ink-muted">
            {t("progression.level.max", { max: progress.max_level })}
          </span>
        )}
      </p>
      <XpBar
        xpInLevel={progress.xp_in_level}
        xpToNext={progress.xp_to_next}
        level={progress.level}
      />
      {progress.boost && (
        <p className="rounded-lg bg-night px-3 py-2 text-sm ring-1 ring-rule" data-testid="boost">
          {t("progression.boost", {
            multiplier: f.number(progress.boost.multiplier, { maximumFractionDigits: 2 }),
            matches: progress.boost.matches_left,
          })}
        </p>
      )}
      <p className="tabular text-sm text-ink-muted" data-testid="solo-cap">
        {t("progression.soloCap", {
          today: f.number(progress.solo_xp_today),
          cap: f.number(progress.solo_xp_cap),
        })}
      </p>
      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-medium">{t("progression.streaks.title")}</h3>
        {progress.streaks.length ? (
          <ul className="flex flex-col gap-1.5" data-testid="streaks">
            {progress.streaks.map((s) => (
              <li key={s.domain} className="flex items-baseline justify-between gap-3 text-sm">
                <span>{t(s.domain === "music" ? "domain.music" : "domain.film")}</span>
                <span className="tabular text-ink-muted">
                  {t("progression.streaks.value", { current: s.current, best: s.best })}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">{t("progression.streaks.empty")}</p>
        )}
      </div>
    </div>
  );
}

function WalletBlock({ wallet }: { wallet: WalletCard }) {
  const t = useTranslations();
  const f = useFormatter();
  return (
    <div className="flex flex-col gap-3">
      <p className="font-display text-4xl font-bold" data-testid="wallet-total">
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
    <div className="grid gap-6 md:grid-cols-2">
      <QuestList title={t("progression.quests.daily")} id="daily" items={quests.daily} />
      <QuestList title={t("progression.quests.weekly")} id="weekly" items={quests.weekly} />
    </div>
  );
}

function QuestList({ title, id, items }: { title: string; id: string; items: QuestCard[] }) {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-sm font-medium">{title}</h3>
      {items.length ? (
        <ul className="flex flex-col gap-3" data-testid={`quests-${id}`}>
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
    <li
      data-testid={`quest-${quest.key}`}
      data-completed={quest.completed}
      className="flex flex-col gap-1.5 rounded-lg bg-night p-3 ring-1 ring-rule"
    >
      <div className="flex items-baseline justify-between gap-3">
        <span dir="auto" className="font-medium">
          {quest.title}
        </span>
        {quest.completed && (
          <span className="text-xs font-medium text-ink-muted">{t("progression.quests.done")}</span>
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
        className="h-1.5 overflow-hidden rounded-full bg-stage"
      >
        <div
          className="h-full bg-[var(--accent,var(--color-music))]"
          style={{ width: `${percent}%` }}
        />
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
    <ul className="flex flex-col gap-2" data-testid="badges">
      {badges.map((b) => {
        const unlocked = b.unlocked_at !== null && b.unlocked_at !== undefined;
        return (
          <li
            key={b.key}
            data-testid={`badge-${b.key}`}
            data-unlocked={unlocked}
            className={cn(
              "flex flex-col gap-0.5 rounded-lg bg-night p-3 ring-1 ring-rule",
              !unlocked && "opacity-80",
            )}
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
