"use client";
/**
 * Профиль `/profile`: шапка с уровнем, вкладки «Обзор / Рейтинг / Внешность / Друзья», ссылка
 * в магазин с балансом Версов. Только пользователю: аноним — 401 и вход, гость — 403
 * `auth.guest_forbidden` и регистрация. Блоки bff гаснут независимо (`unavailable`) — страница
 * при этом цела.
 */
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { AppearanceCard, ProfilePage, ProgressCard } from "@/shared/api/types";
import { blockData } from "@/shared/api/blocks";
import { Button } from "@/shared/ui/button";
import { Row } from "@/shared/ui/row";
import { TabPanel, Tabs, type TabItem } from "@/shared/ui/tabs";
import { useSessionInfo, useViewer } from "@/features/auth/session";
import { Avatar, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import { itemOf } from "@/features/cosmetics/slots";
import { useHomePage } from "@/features/daily/DailyCards";
import { FriendsList } from "@/features/friends/FriendsView";
import { friendsKeys, fetchFriends } from "@/features/friends/api";
import { useOnlinePage } from "@/features/online/bff";
import { RatingPanel } from "@/features/rating/RatingPanel";
import { fetchProfilePage, profileQueryKey } from "./api";
import { isMaxLevel } from "./logic";
import { AvatarBlock } from "./AvatarBlock";
import { ProfileOverview } from "./ProfileOverview";
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

type TabId = "overview" | "rating" | "look" | "friends";

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

function ProfileScreen({ blocks }: { blocks: ProfilePage["blocks"] }) {
  const t = useTranslations();
  const f = useFormatter();
  const [tab, setTab] = useState<TabId>("overview");
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
  const tabs: TabItem<TabId>[] = [
    { value: "overview", label: t("progression.tabs.overview") },
    { value: "rating", label: t("progression.tabs.rating") },
    { value: "look", label: t("progression.tabs.look") },
    {
      value: "friends",
      label:
        friends.data === undefined
          ? t("progression.tabs.friends")
          : t("progression.tabs.friendsCount", { count: friends.data.items.length }),
    },
  ];
  const avatar = blocks.avatar?.status === "ok" ? blocks.avatar.data : null;

  return (
    <div className="flex flex-col gap-5 pt-2">
      <ProfileHeader appearance={appearance} progress={progress} onEdit={() => setTab("look")} />
      <div className="flex items-end border-b-[1.5px] border-rule">
        <Tabs
          tabs={tabs}
          value={tab}
          onChange={setTab}
          idBase="profile"
          aria-label={t("progression.tabs.label")}
          className="min-w-0 flex-1 border-b-0"
        />
        {wallet && (
          <Link
            href="/shop"
            data-testid="nav-shop"
            aria-label={t("progression.pillLabel", { total: f.number(wallet.total) })}
            className="inline-flex h-11 shrink-0 items-center px-1 pl-3 text-[15px] font-medium whitespace-nowrap text-music"
          >
            {t("progression.pill", { total: f.number(wallet.total) })}
          </Link>
        )}
      </div>
      <TabPanel idBase="profile" value="overview" active={tab === "overview"}>
        <ProfileOverview blocks={blocks} ratings={ratings} playedToday={playedToday} />
      </TabPanel>
      <TabPanel idBase="profile" value="rating" active={tab === "rating"}>
        <div className="flex flex-col gap-4">
          <RatingPanel ratings={ratings} seasons={seasons} />
          <Row href="/leaderboards" data-testid="nav-leaderboards">
            {t("progression.ratingTab.all")}
          </Row>
        </div>
      </TabPanel>
      <TabPanel idBase="profile" value="look" active={tab === "look"}>
        <div className="flex flex-col gap-4">
          {avatar ? (
            <section aria-labelledby="avatar-title" className="flex flex-col gap-2.5">
              <h2 id="avatar-title" className="px-0.5 font-semibold">
                {t("avatar.title")}
              </h2>
              <AvatarBlock avatar={avatar} currentUrl={appearance?.custom_avatar_url} />
            </section>
          ) : (
            blocks.avatar != null && (
              <p className="text-ink-muted" data-testid="block-avatar-unavailable">
                {t("progression.blockUnavailable")}
              </p>
            )
          )}
          <Row href="/shop" data-testid="to-shop">
            {t("progression.look.shop")}
          </Row>
        </div>
      </TabPanel>
      <TabPanel idBase="profile" value="friends" active={tab === "friends"}>
        <div className="flex flex-col gap-4">
          <FriendsList />
          <Row href="/friends" data-testid="nav-friends">
            {t("progression.friendsTab.all")}
          </Row>
        </div>
      </TabPanel>
    </div>
  );
}

/** Шапка: аватар с рамкой и фоном, ник, титул, уровень, полоса опыта и «Изменить». */
function ProfileHeader({
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
  return (
    <ProfileBackdrop appearance={appearance} className="rounded-[var(--radius-ticket)]">
      <section className="flex items-center gap-4" data-testid="player-badge">
        <Avatar nickname={name} appearance={appearance} size="lg" />
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <div className="flex min-w-0 items-baseline gap-2.5">
            <h1
              className="truncate font-display text-[26px] leading-none font-extrabold"
              dir="auto"
              data-testid="player-nickname"
            >
              {name}
            </h1>
            {title && (
              <span
                className="truncate text-sm text-ink-muted"
                dir="auto"
                data-testid="player-title"
              >
                {title.title}
              </span>
            )}
          </div>
          {progress && (
            <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 sm:flex-nowrap">
              <span
                className="text-[13px] whitespace-nowrap text-ink-muted"
                data-testid="level-value"
              >
                {t("progression.level.value", { level: progress.level })}
                {isMaxLevel(progress) &&
                  ` · ${t("progression.level.max", { max: progress.max_level })}`}
              </span>
              <div className="order-last min-w-0 basis-full sm:order-none sm:flex-1 sm:basis-0">
                <XpBar
                  xpInLevel={progress.xp_in_level}
                  xpToNext={progress.xp_to_next}
                  level={progress.level}
                  bare
                />
              </div>
              <span className="tabular text-[13px] whitespace-nowrap text-ink-muted">
                {progress.xp_to_next === null
                  ? t("progression.header.xpMax")
                  : t("progression.header.xpLeft", {
                      left: f.number(progress.xp_to_next),
                      next: progress.level + 1,
                    })}
              </span>
            </div>
          )}
        </div>
        <Button variant="secondary" size="sm" onClick={onEdit} data-testid="profile-edit">
          {t("progression.header.edit")}
        </Button>
      </section>
    </ProfileBackdrop>
  );
}
