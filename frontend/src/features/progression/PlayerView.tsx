"use client";
/**
 * Профиль игрока `/players/[id]` на макете профиля v3 без кнопок редактирования: слева карточка и
 * действия, справа статистика и бейджи. Публичная часть прогресса — уровень, всего опыта, полученные
 * бейджи, действия над игроком. Только пользователю с аккаунтом (аноним — вход, гость —
 * регистрация); нет игрока — 404; недоступный progression — «появится позже». Названия бейджей —
 * из данных API.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import { blockData } from "@/shared/api/blocks";
import type { AppearanceCard } from "@/shared/api/types";
import { StatTile } from "@/shared/ui/stat-tile";
import { Tile } from "@/shared/ui/tile";
import { ListRow } from "@/shared/ui/list-row";
import { useViewer } from "@/features/auth/session";
import { Avatar, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import { itemOf } from "@/features/cosmetics/slots";
import { PlayerActions } from "@/features/friends/PlayerActions";
import { ReportButton } from "@/features/reports/ReportDialog";
import { useMyUserId } from "@/features/auth/use-my-user-id";
import { fetchPlayerPage, playerQueryKey } from "./api";
import { gateOf, ProfileGate } from "./ProfileView";

export function PlayerView({ userId }: { userId: string }) {
  const t = useTranslations();
  const f = useFormatter();
  const viewer = useViewer();
  const myId = useMyUserId();
  const page = useQuery({
    queryKey: playerQueryKey(userId),
    queryFn: () => fetchPlayerPage(userId),
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <ProfileGate variant={viewer.kind === "guest" ? "guest" : "login"} />;
  if (page.isError && !page.data) {
    const err = isApiError(page.error) ? page.error : null;
    const variant = gateOf(err?.status ?? null, err?.code ?? null);
    if (variant) return <ProfileGate variant={variant} />;
    return (
      <div className="flex flex-col items-start gap-3 pt-12" data-testid="player-error">
        <p className="text-ink-muted">
          {err?.status === 404 ? t("progression.player.notFound") : t("progression.unavailable")}
        </p>
        <Link href="/" className="text-music underline underline-offset-4">
          {t("progression.player.back")}
        </Link>
      </div>
    );
  }
  if (!page.data)
    return (
      <p className="pt-6 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  const block = page.data.blocks.progress;
  const data = block?.status === "ok" ? block.data : null;
  // Оформление необязательно: `null` или недоступный блок — игрок без рамки, титула и фона.
  const appearance = blockData(page.data.blocks.appearance);
  const social = blockData(page.data.blocks.social);
  // Ник из social (ADR 0069); нет ника (скрытый профиль, свой профиль, social недоступен) - «Игрок».
  const nickname = page.data.nickname ?? t("nav.player");
  const avatarAsset = userId === myId ? null : (appearance?.custom_avatar_asset_id ?? null);
  // 5c: профиль закрыт, игрок заблокировал зрителя или неизвестен — ответы неразличимы.
  if (page.data.restricted) {
    return (
      <div className="flex max-w-[640px] flex-col gap-5 pt-2" data-testid="player-restricted">
        <PlayerHeader nickname={nickname} appearance={appearance} level={null} />
        <p className="max-w-[60ch] text-ink-muted">{t("social.player.restricted")}</p>
        {social?.blocked_by_me && <PlayerActions userId={userId} social={social} />}
      </div>
    );
  }
  return (
    <div
      className="grid items-start gap-5 pt-2 lg:grid-cols-[300px_minmax(0,1fr)]"
      data-testid="player"
    >
      <div className="flex min-w-0 flex-col gap-5">
        <PlayerHeader nickname={nickname} appearance={appearance} level={data?.level ?? null} />
        {social && <PlayerActions userId={userId} social={social} />}
        {avatarAsset && (
          <div>
            <ReportButton
              kind="avatar"
              targetId={avatarAsset}
              label={t("social.report.avatar")}
              ariaLabel={t("social.report.avatarLabel", { name: nickname })}
              testId="report-avatar"
            />
          </div>
        )}
      </div>
      {!data ? (
        <p className="text-ink-muted" data-testid="player-unavailable">
          {t("progression.blockUnavailable")}
        </p>
      ) : (
        <div className="flex min-w-0 flex-col gap-5">
          <Tile cap={t("progression.cols.stats")}>
            <div className="grid grid-cols-2 gap-2">
              <StatTile accent value={data.level} label={t("progression.level.title")} />
              <StatTile
                value={<span data-testid="player-xp">{f.number(data.total_xp)}</span>}
                label={t("progression.tiles.xp")}
              />
            </div>
          </Tile>
          <Tile cap={t("progression.player.badges")}>
            {data.badges.length ? (
              <ul className="flex flex-col gap-1.5" data-testid="player-badges">
                {data.badges.map((b) => (
                  <li key={b.key} data-testid={`player-badge-${b.key}`}>
                    <ListRow
                      trailing={t("progression.player.unlockedAt", {
                        date: f.dateTime(new Date(b.unlocked_at), { dateStyle: "long" }),
                      })}
                      className="min-h-11"
                    >
                      <span dir="auto" className="font-semibold">
                        {b.title}
                      </span>
                    </ListRow>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-ink-muted">{t("progression.player.noBadges")}</p>
            )}
          </Tile>
        </div>
      )}
    </div>
  );
}

/** Шапка чужого профиля: аватар с рамкой, ник Unbounded, титул и уровень. */
function PlayerHeader({
  nickname,
  appearance,
  level,
}: {
  nickname: string;
  appearance: AppearanceCard | null;
  level: number | null;
}) {
  const t = useTranslations();
  const title = itemOf(appearance, "title");
  return (
    <ProfileBackdrop appearance={appearance} className="rounded-[var(--radius-ticket)]">
      <Tile className="bg-transparent" data-testid="player-badge">
        <header className="flex items-center gap-3.5">
          <Avatar nickname={nickname} appearance={appearance} size="lg" />
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1
              className="truncate font-display text-lg leading-tight font-extrabold"
              dir="auto"
              data-testid="player-nickname"
            >
              {nickname}
            </h1>
            <p className="truncate text-sm text-ink-muted">
              {title && (
                <span dir="auto" data-testid="player-title">
                  {title.title}
                </span>
              )}
              {title && level != null && " · "}
              {level != null && (
                <span data-testid="player-level">{t("progression.level.value", { level })}</span>
              )}
            </p>
          </div>
        </header>
      </Tile>
    </ProfileBackdrop>
  );
}
