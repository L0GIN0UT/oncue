"use client";
/**
 * Игроки комнаты местами (по макету Room): аватар, ник, хост, присутствие («Отключён», «Отошёл»),
 * в лобби — готовность, в матче — статус раунда («Думает / Ответил / Мимо / Угадал») и очки.
 * Свободные места лобби — пунктирные плашки. Текста чужих ответов здесь нет и быть не может:
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
  thinking: "border-rule text-ink-muted",
  answered: "border-partial-line bg-partial-fill text-ink",
  missed: "border-miss-line bg-miss-fill text-ink",
  guessed: "border-exact-line bg-exact-fill text-ink",
} as const;

type Props = {
  players: PlayerSlot[];
  matchPlayers?: MatchPlayerView[] | null;
  myId: string | null;
  /** Последняя эмоция или фраза каждого игрока (уже отфильтрована по свежести). */
  bubbles?: Map<string, FeedItem>;
  showReady?: boolean;
  /** Мест в комнате: недостающие рисуются пунктирными «свободно» (лобби). */
  seats?: number;
};

export function PlayersRail({ players, matchPlayers, myId, bubbles, showReady, seats }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const byUser = new Map((matchPlayers ?? []).map((p) => [p.user_id, p]));
  const sorted = [...players].sort((a, b) => a.slot - b.slot);
  // Оформление необязательно: нет ответа или отказ — ник без аватара и рамки.
  const appearances = usePlayersAppearance(players.map((p) => p.user_id));
  const free = Math.max(0, (seats ?? 0) - players.filter((p) => p.presence !== "left").length);
  return (
    <ul
      className="grid gap-2.5 sm:grid-cols-2"
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
              "relative flex min-h-[60px] items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] bg-stage px-4 py-2",
              p.user_id === myId ? "border-music" : "border-rule",
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
                      <Crown className="size-4 shrink-0 text-music" aria-label={t("match.host")} />
                    )}
                    {p.user_id === myId && (
                      <span className="text-sm text-ink-faint">{t("match.you")}</span>
                    )}
                  </>
                }
              />
              <span className="flex flex-wrap items-center gap-2 pl-[52px] text-xs text-ink-muted empty:hidden">
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
              </span>
            </div>
            {showReady && p.presence === "online" && (
              <span
                data-testid="ready-state"
                className={cn("text-[13px] lowercase", p.ready ? "text-exact" : "text-ink-muted")}
              >
                {p.ready ? t("match.lobby.ready") : t("match.lobby.notReady")}
              </span>
            )}
            {mp?.status && (
              <span
                className={cn(
                  "rounded-full border-[1.5px] px-2.5 py-0.5 text-xs font-medium",
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
                className="absolute -top-3 right-3 max-w-[80%] truncate rounded-full bg-ink px-2.5 py-0.5 text-xs font-medium text-night"
                data-testid="emote-bubble"
              >
                {feedText(bubble, dt)}
              </span>
            )}
          </li>
        );
      })}
      {Array.from({ length: free }, (_, i) => (
        <li
          key={`free-${i}`}
          aria-hidden
          className="grid min-h-[60px] place-items-center rounded-[var(--radius-control)] border-[1.5px] border-dashed border-rule text-sm text-ink-muted"
        >
          {t("match.lobby.free")}
        </li>
      ))}
    </ul>
  );
}

/** Текст эмоции или фразы (ключ i18n из конфига режима); свободный чат — как есть. */
export function feedText(item: FeedItem, dt: (key: string, fallback: string) => string): string {
  if (item.kind === "chat") return item.text;
  const key = item.emote ?? item.phrase ?? "";
  return dt(key, key.split(".").at(-1) ?? "");
}
