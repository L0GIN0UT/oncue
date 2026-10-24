"use client";
/**
 * Профиль игрока `/players/[id]` (этап 5a): публичная часть прогресса — уровень, всего опыта,
 * полученные бейджи. Только пользователю с аккаунтом (аноним — вход, гость — регистрация); нет
 * игрока — 404; недоступный progression — «появится позже». Названия бейджей — из данных API.
 */
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import { blockData } from "@/shared/api/blocks";
import { useViewer } from "@/features/auth/session";
import { PlayerBadge, ProfileBackdrop } from "@/features/cosmetics/PlayerBadge";
import { fetchPlayerPage, playerQueryKey } from "./api";
import { gateOf, ProfileGate } from "./ProfileView";

export function PlayerView({ userId }: { userId: string }) {
  const t = useTranslations();
  const f = useFormatter();
  const viewer = useViewer();
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
        <Link href="/" className="underline underline-offset-4">
          {t("progression.player.back")}
        </Link>
      </div>
    );
  }
  const block = page.data?.blocks.progress;
  const data = block?.status === "ok" ? block.data : null;
  // Оформление необязательно: `null` или недоступный блок — игрок без рамки, титула и фона.
  const appearance = blockData(page.data?.blocks.appearance);
  return (
    <div className="flex flex-col gap-6 pt-6 sm:pt-10">
      <h1 className="font-display text-3xl font-bold">{t("progression.player.title")}</h1>
      {!block ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : !data ? (
        <p className="text-ink-muted" data-testid="player-unavailable">
          {t("progression.blockUnavailable")}
        </p>
      ) : (
        <>
          <ProfileBackdrop
            appearance={appearance}
            className="rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
          >
            <PlayerBadge nickname={t("nav.player")} appearance={appearance} size="lg" />
          </ProfileBackdrop>
          <p className="font-display text-4xl font-bold" data-testid="player-level">
            {t("progression.level.value", { level: data.level })}
          </p>
          <p className="tabular text-ink-muted" data-testid="player-xp">
            {t("progression.player.totalXp", { xp: f.number(data.total_xp) })}
          </p>
          <section aria-labelledby="player-badges" className="flex flex-col gap-3">
            <h2 id="player-badges" className="font-display text-xl font-semibold">
              {t("progression.player.badges")}
            </h2>
            {data.badges.length ? (
              <ul className="grid gap-2 sm:grid-cols-2" data-testid="player-badges">
                {data.badges.map((b) => (
                  <li
                    key={b.key}
                    data-testid={`player-badge-${b.key}`}
                    className="flex flex-col gap-0.5 rounded-lg bg-stage p-3 ring-1 ring-rule"
                  >
                    <span dir="auto" className="font-medium">
                      {b.title}
                    </span>
                    <span className="text-xs text-ink-muted">
                      {t("progression.player.unlockedAt", {
                        date: f.dateTime(new Date(b.unlocked_at), { dateStyle: "long" }),
                      })}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-ink-muted">{t("progression.player.noBadges")}</p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
