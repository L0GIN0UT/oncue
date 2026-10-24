"use client";
/**
 * Игроки комнаты: ник, хост, присутствие («Отключён», «Отошёл»), в матче — статус раунда
 * («Думает / Ответил / Мимо / Угадал») и очки. Текста чужих ответов здесь нет и быть не может:
 * сервер отдаёт только статус (античит §12). Последняя эмоция игрока — пузырь у его места.
 */
import { useTranslations } from "next-intl";
import { Crown, WifiOff } from "lucide-react";
import type { MatchPlayerView, PlayerSlot } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { usePlayersAppearance } from "@/features/cosmetics/api";
import { PlayerBadge } from "@/features/cosmetics/PlayerBadge";
import type { FeedItem } from "./room-store";

const STATUS_STYLE = {
  thinking: "bg-stage-raised text-ink-muted ring-1 ring-rule ring-inset",
  answered: "bg-partial/20 text-partial",
  missed: "bg-miss/20 text-miss",
  guessed: "bg-exact/20 text-exact",
} as const;

type Props = {
  players: PlayerSlot[];
  matchPlayers?: MatchPlayerView[] | null;
  myId: string | null;
  /** Последняя эмоция или фраза каждого игрока (уже отфильтрована по свежести). */
  bubbles?: Map<string, FeedItem>;
  showReady?: boolean;
};

export function PlayersRail({ players, matchPlayers, myId, bubbles, showReady }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const byUser = new Map((matchPlayers ?? []).map((p) => [p.user_id, p]));
  const sorted = [...players].sort((a, b) => a.slot - b.slot);
  // Оформление необязательно: нет ответа или отказ — ник без аватара и рамки.
  const appearances = usePlayersAppearance(players.map((p) => p.user_id));
  return (
    <ul
      className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
      aria-label={t("match.players")}
      data-testid="players"
    >
      {sorted.map((p) => {
        const mp = byUser.get(p.user_id);
        const bubble = bubbles?.get(p.user_id);
        const gone = p.presence !== "online";
        return (
          <li
            key={p.user_id}
            data-testid={`player-${p.nickname}`}
            className={cn(
              "relative flex items-center gap-3 rounded-[var(--radius-control)] bg-stage px-4 py-3 ring-1 ring-rule",
              p.user_id === myId && "ring-[var(--accent)]",
              gone && "opacity-60",
            )}
          >
            <div className="flex min-w-0 flex-1 flex-col">
              <PlayerBadge
                nickname={p.nickname}
                appearance={appearances.get(p.user_id)}
                suffix={
                  <>
                    {p.is_host && (
                      <Crown
                        className="size-4 shrink-0 text-[var(--color-music)]"
                        aria-label={t("match.host")}
                      />
                    )}
                    {p.user_id === myId && (
                      <span className="text-sm text-ink-faint">{t("match.you")}</span>
                    )}
                  </>
                }
              />
              <span className="flex flex-wrap items-center gap-2 text-sm text-ink-muted">
                {p.presence === "disconnected" && (
                  <span
                    className="flex items-center gap-1 text-miss"
                    data-testid="presence-disconnected"
                  >
                    <WifiOff className="size-3.5" aria-hidden />
                    {t("match.presence.disconnected")}
                  </span>
                )}
                {p.presence === "left" && <span>{t("match.presence.left")}</span>}
                {p.away && p.presence === "online" && <span>{t("match.away")}</span>}
                {showReady && p.presence === "online" && (
                  <span data-testid="ready-state" className={p.ready ? "text-exact" : undefined}>
                    {p.ready ? t("match.lobby.ready") : t("match.lobby.notReady")}
                  </span>
                )}
              </span>
            </div>
            {mp?.status && (
              <span
                className={cn(
                  "rounded-full px-2.5 py-1 text-xs font-medium",
                  STATUS_STYLE[mp.status],
                )}
                data-testid="player-status"
                data-status={mp.status}
              >
                {t(`match.status.${mp.status}`)}
              </span>
            )}
            {matchPlayers && (
              <span
                className="tabular font-display text-lg font-semibold"
                data-testid="player-score"
              >
                <span className="sr-only">{t("game.score")}: </span>
                {mp?.score ?? p.score}
              </span>
            )}
            {bubble && (
              <span
                role="status"
                className="absolute -top-3 right-3 max-w-[80%] truncate rounded-full bg-ink px-2.5 py-0.5 text-xs font-medium text-night shadow"
                data-testid="emote-bubble"
              >
                {feedText(bubble, dt)}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Текст эмоции или фразы (ключ i18n из конфига режима); свободный чат — как есть. */
export function feedText(item: FeedItem, dt: (key: string, fallback: string) => string): string {
  if (item.kind === "chat") return item.text;
  const key = item.emote ?? item.phrase ?? "";
  return dt(key, key.split(".").at(-1) ?? "");
}
