"use client";
/**
 * Профиль `/profile` по макету v3 (Profile): три колонки — игрок, внешность и друзья слева;
 * рейтинг, статистика, календарь и серии по центру; задания, бейджи, колесо и переходы справа.
 * Только пользователю: аноним — 401 и вход, гость — 403 `auth.guest_forbidden` и регистрация.
 * Блоки bff гаснут независимо (`unavailable`) — страница при этом цела.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { AppearanceCard, ProfilePage, ProgressCard } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { Avatar as AvatarCircle } from "@/shared/ui/avatar";
import { Button } from "@/shared/ui/button";
import { Tile } from "@/shared/ui/tile";
import { useSessionInfo, useViewer } from "@/features/auth/session";
import { Avatar, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import { itemOf } from "@/features/cosmetics/slots";
import { useHomePage } from "@/features/daily/DailyCards";
import { friendsKeys, fetchFriends } from "@/features/friends/api";
import { useOnlinePage } from "@/features/online/bff";
import { fetchProfilePage, profileQueryKey } from "./api";
import { isMaxLevel } from "./logic";
import { AvatarBlock } from "./AvatarBlock";
import { ProfileCenter, ProfileRight } from "./ProfileOverview";
import { XpBar } from "./XpBar";

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
        <h1 className="font-display text-[28px] font-extrabold">
          {t("progression.forbidden.title")}
        </h1>
        <p className="text-ink-muted">{t("progression.forbidden.text")}</p>
      </div>
    );
  }
  const guest = variant === "guest";
  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12" data-testid="profile-gate">
      <h1 className="font-display text-[28px] font-extrabold">
        {t("progression.usersOnly.title")}
      </h1>
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
  return blocks ? (
    <ProfileScreen blocks={blocks} />
  ) : (
    <p className="pt-6 text-ink-muted" role="status">
      {t("common.loading")}
    </p>
  );
}

const LOOK_ID = "profile-look";

function ProfileScreen({ blocks }: { blocks: ProfilePage["blocks"] }) {
  const t = useTranslations();
  const f = useFormatter();
  const appearance = blockData(blocks.appearance);
  const progress = blockData(blocks.progress);
  const wallet = blockData(blocks.wallet);
  const online = useOnlinePage();
  const ratings = blockData(online.data?.blocks?.ratings);
  const seasons = blockData(online.data?.blocks?.seasons);
  const home = useHomePage(null);
  const playedToday =
    blockData(home.data?.blocks.dailies)?.some((c) => c.status === "finished") ?? false;
  const friends = useQuery({ queryKey: friendsKeys.list, queryFn: fetchFriends });
  const avatar = blocks.avatar?.status === "ok" ? blocks.avatar.data : null;

  return (
    <div
      className="grid items-start gap-5 pt-2 lg:grid-cols-[300px_minmax(0,1fr)_320px]"
      data-testid="profile"
    >
      <div className="flex min-w-0 flex-col gap-5">
        <PlayerCard
          appearance={appearance}
          progress={progress}
          onEdit={() => {
            const el = document.getElementById(LOOK_ID);
            el?.scrollIntoView({ block: "center", behavior: "smooth" });
            el?.focus({ preventScroll: true });
          }}
        />
        <Tile cap={t("progression.cols.look")} data-testid="block-look">
          <div id={LOOK_ID} tabIndex={-1} className="flex flex-col gap-3 outline-none">
            {avatar ? (
              <section data-testid="block-avatar" aria-label={t("avatar.title")}>
                <AvatarBlock avatar={avatar} currentUrl={appearance?.custom_avatar_url} />
              </section>
            ) : (
              blocks.avatar != null && (
                <p className="text-sm text-ink-muted" data-testid="block-avatar-unavailable">
                  {t("progression.blockUnavailable")}
                </p>
              )
            )}
          </div>
          <Link href="/shop" data-testid="nav-shop" className="text-xs font-semibold text-music">
            {wallet
              ? t("progression.cols.shopBalance", { total: f.number(wallet.total) })
              : t("progression.look.shop")}
          </Link>
        </Tile>
        <Tile
          cap={t("progression.cols.friends")}
          aside={
            friends.data && (
              <span className="rounded-full bg-stage-raised px-[9px] py-[3px] text-xs font-semibold text-ink-muted">
                {friends.data.items.length}
              </span>
            )
          }
          data-testid="profile-friends"
        >
          {friends.data && friends.data.items.length > 0 && (
            <ul className="flex justify-between gap-2">
              {friends.data.items.slice(0, 5).map((fr) => (
                <li key={fr.user_id} title={fr.nickname} className="relative">
                  <AvatarCircle name={fr.nickname} />
                  {fr.status !== "offline" && (
                    <span
                      aria-hidden
                      className="absolute right-0 bottom-0 size-[11px] rounded-full border-2 border-stage bg-exact"
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
          <Link
            href="/friends"
            data-testid="nav-friends"
            className="grid h-[34px] place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised text-[13px] font-semibold hover:border-rule-strong"
          >
            {t("progression.cols.addFriend")}
          </Link>
        </Tile>
      </div>
      <ProfileCenter
        blocks={blocks}
        ratings={ratings}
        seasons={seasons}
        playedToday={playedToday}
      />
      <ProfileRight blocks={blocks} />
    </div>
  );
}

/** Карточка игрока: аватар с рамкой и фоном, ник, титул, уровень, полоса опыта, серия и «Изменить». */
function PlayerCard({
  appearance,
  progress,
  onEdit,
}: {
  appearance: AppearanceCard | null;
  progress: ProgressCard | null;
  onEdit: () => void;
}) {
  const t = useTranslations();
  const f = useFormatter();
  const { data } = useSessionInfo();
  const name = data?.subject?.display_name ?? t("nav.player");
  const title = itemOf(appearance, "title");
  const streak = Math.max(0, ...(progress?.streaks.map((s) => s.current) ?? []));
  return (
    <ProfileBackdrop appearance={appearance} className="rounded-[var(--radius-ticket)]">
      <Tile className="bg-transparent" data-testid="player-badge">
        <div className="flex items-center gap-3.5">
          <Avatar nickname={name} appearance={appearance} size="lg" />
          <div className="flex min-w-0 flex-col gap-1">
            <h1
              className="truncate font-display text-lg leading-tight font-extrabold"
              dir="auto"
              data-testid="player-nickname"
            >
              {name}
            </h1>
            {title && (
              <span
                className="w-max max-w-full truncate rounded-full bg-glow px-[9px] py-[3px] text-xs font-semibold text-music"
                dir="auto"
                data-testid="player-title"
              >
                {title.title}
              </span>
            )}
          </div>
        </div>
        {progress && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-[13px]">
              <span className="font-semibold" data-testid="level-value">
                {t("progression.level.value", { level: progress.level })}
                {isMaxLevel(progress) &&
                  ` · ${t("progression.level.max", { max: progress.max_level })}`}
              </span>
              <span className="tabular text-[11px] text-ink-muted">
                {progress.xp_to_next === null
                  ? t("progression.header.xpMax")
                  : t("progression.header.xpLeft", {
                      left: f.number(progress.xp_to_next),
                      next: progress.level + 1,
                    })}
              </span>
            </div>
            <XpBar
              xpInLevel={progress.xp_in_level}
              xpToNext={progress.xp_to_next}
              level={progress.level}
              bare
            />
          </div>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="text-[13px] text-ink-soft">
            {t("progression.cols.streakDays", { count: streak })}
          </span>
          <Button variant="secondary" size="sm" onClick={onEdit} data-testid="profile-edit">
            {t("progression.header.edit")}
          </Button>
        </div>
      </Tile>
    </ProfileBackdrop>
  );
}
