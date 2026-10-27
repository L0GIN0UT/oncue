"use client";
/**
 * Полоса счёта дуэли (макет Duel): два игрока с точками раундов, счёт посередине и номер раунда.
 * Точки раундов копятся из ревилов, увиденных на этом экране: сервер не отдаёт историю раундов,
 * поэтому после перезагрузки страницы сыгранные раунды показываются пустыми точками (в handoff
 * записано как «нужно от бэкенда»).
 */
import { useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { WifiOff } from "lucide-react";
import type { MatchPlayerView, PlayerSlot } from "@/shared/api/types";
import type { RoundRevealDelta } from "@/shared/realtime";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { usePlayersAppearance } from "@/features/cosmetics/api";
import { Avatar } from "@/features/cosmetics/PlayerBadge";
import type { AppearanceLike } from "@/features/cosmetics/slots";
import { feedText } from "./PlayersRail";
import type { FeedItem } from "./room-store";

/** Победитель раунда: id игрока, `null` — ничья, нет записи — раунд не наблюдался. */
export type RoundWinners = ReadonlyMap<number, string | null>;

/** Накапливает победителей раундов по ревилам: у кого раунд взят выше по очкам. */
export function useRoundWinners(reveal: RoundRevealDelta | null): RoundWinners {
  const [winners, setWinners] = useState<RoundWinners>(new Map());
  // Производное состояние от пропса: запись о раунде появляется один раз при новом ревиле.
  if (reveal && !winners.has(reveal.round_index)) {
    const top = Math.max(...reveal.results.map((r) => r.score));
    const leaders = reveal.results.filter((r) => r.score === top && top > 0);
    const next = new Map(winners);
    next.set(reveal.round_index, leaders.length === 1 ? leaders[0]!.user_id : null);
    setWinners(next);
  }
  return winners;
}

type Props = {
  players: PlayerSlot[];
  matchPlayers?: MatchPlayerView[] | null;
  myId: string | null;
  roundIndex: number;
  roundsTotal: number;
  winners: RoundWinners;
  bubbles: Map<string, FeedItem>;
};

export function ScoreStrip({
  players,
  matchPlayers,
  myId,
  roundIndex,
  roundsTotal,
  winners,
  bubbles,
}: Props) {
  const t = useTranslations();
  const seated = [...players].filter((p) => p.presence !== "left").sort((a, b) => a.slot - b.slot);
  const mine = seated.find((p) => p.user_id === myId) ?? seated[0];
  const rival = seated.find((p) => p !== mine);
  const appearances = usePlayersAppearance(seated.map((p) => p.user_id));
  const score = (p: PlayerSlot | undefined) =>
    p ? (matchPlayers?.find((m) => m.user_id === p.user_id)?.score ?? p.score) : 0;
  if (!mine) return null;

  return (
    <section
      aria-label={t("match.strip.label")}
      data-testid="score-strip"
      className="flex items-center gap-[18px] rounded-[20px] border-[1.5px] border-rule bg-stage px-[18px] py-3.5"
    >
      <StripPlayer
        player={mine}
        side="left"
        isMe={mine.user_id === myId}
        score={score(mine)}
        status={matchPlayers?.find((m) => m.user_id === mine.user_id)?.status ?? null}
        appearance={appearances.get(mine.user_id)}
        bubble={bubbles.get(mine.user_id)}
        dots={
          <RoundDots id={mine.user_id} total={roundsTotal} winners={winners} current={roundIndex} />
        }
      />
      <div className="flex flex-col items-center gap-0.5" data-testid="duel-score">
        <span className="tabular font-display text-[28px] leading-none font-extrabold tracking-tight">
          {score(mine)} : {score(rival)}
        </span>
        <span className="text-xs whitespace-nowrap text-ink-muted">
          {t("match.strip.round", { index: roundIndex + 1, total: roundsTotal })}
        </span>
      </div>
      {rival ? (
        <StripPlayer
          player={rival}
          side="right"
          isMe={rival.user_id === myId}
          score={score(rival)}
          status={matchPlayers?.find((m) => m.user_id === rival.user_id)?.status ?? null}
          appearance={appearances.get(rival.user_id)}
          bubble={bubbles.get(rival.user_id)}
          dots={
            <RoundDots
              id={rival.user_id}
              total={roundsTotal}
              winners={winners}
              current={roundIndex}
            />
          }
        />
      ) : (
        <div className="min-w-0 flex-1" />
      )}
    </section>
  );
}

function StripPlayer({
  player,
  side,
  isMe,
  score,
  status,
  appearance,
  bubble,
  dots,
}: {
  player: PlayerSlot;
  side: "left" | "right";
  isMe: boolean;
  score: number;
  status: MatchPlayerView["status"];
  appearance: AppearanceLike | undefined;
  bubble: FeedItem | undefined;
  dots: ReactNode;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const gone = player.presence !== "online";
  return (
    <div
      data-testid={`player-${player.nickname}`}
      className={cn(
        "relative flex min-w-0 flex-1 items-center gap-3",
        side === "right" && "flex-row-reverse text-right",
        gone && "opacity-60",
      )}
    >
      <Avatar nickname={player.nickname} appearance={appearance} size="md" />
      <div className="sr-only">
        <span data-testid="player-score">
          {t("game.score")}: {score}
        </span>
        {status && <span data-testid="player-status">{t(`match.status.${status}`)}</span>}
      </div>
      <div className={cn("flex min-w-0 flex-col gap-1.5", side === "right" && "items-end")}>
        <span className="truncate font-semibold" dir="auto" data-testid="player-nickname">
          {isMe ? t("match.strip.you") : player.nickname}
        </span>
        {dots}
        {player.presence === "disconnected" && (
          <span
            className="flex items-center gap-1 text-xs text-miss"
            data-testid="presence-disconnected"
          >
            <WifiOff className="size-3.5" aria-hidden />
            {t("match.presence.disconnected")}
          </span>
        )}
        {player.away && player.presence === "online" && (
          <span className="text-xs text-ink-muted">{t("match.away")}</span>
        )}
      </div>
      {bubble && (
        <span
          role="status"
          className="absolute -top-6 max-w-[80%] truncate rounded-full bg-ink px-2.5 py-0.5 text-xs font-medium text-night"
          data-testid="emote-bubble"
        >
          {feedText(bubble, dt)}
        </span>
      )}
    </div>
  );
}

function RoundDots({
  id,
  total,
  winners,
  current,
}: {
  id: string;
  total: number;
  winners: RoundWinners;
  /** Идущий сейчас раунд: янтарная рамка у ещё не сыгранного кружка. */
  current: number;
}) {
  const t = useTranslations();
  const results = Array.from({ length: total }, (_, i) => {
    if (!winners.has(i)) return "pending" as const;
    const winner = winners.get(i);
    return winner === null
      ? ("draw" as const)
      : winner === id
        ? ("won" as const)
        : ("lost" as const);
  });
  const won = results.filter((r) => r === "won").length;
  const lost = results.filter((r) => r === "lost").length;
  return (
    <span
      role="img"
      aria-label={t("match.strip.dots", { won, lost })}
      className="flex gap-1.5"
      data-testid="round-dots"
    >
      {results.map((r, i) => (
        <i
          key={i}
          data-result={r}
          data-current={r === "pending" && i === current ? "" : undefined}
          className={cn(
            "block size-3.5 rounded-full border-[1.5px]",
            r === "won" && "border-exact-line bg-exact-fill",
            r === "lost" && "border-miss-line bg-miss-fill",
            r === "draw" && "border-rule-strong bg-skip-fill",
            r === "pending" &&
              (i === current ? "border-music bg-skip-fill" : "border-rule bg-skip-fill"),
          )}
        />
      ))}
    </span>
  );
}
