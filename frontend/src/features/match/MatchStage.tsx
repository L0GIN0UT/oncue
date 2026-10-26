"use client";
/**
 * Экран матча: загадка уровня (реестр `clueRegistry`, сцена — по `clue_type`), ответ (реестр
 * форматов матча по `answer_format`), таймер по `phase_ends_at`, статусы игроков, эмоции и
 * чат, ревил. Уровень приходит только после открытия (`level_opened`), исход своего ответа —
 * только в ответе команды; время и очки считает сервер.
 */
import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { Coffee, Flag } from "lucide-react";
import type { MatchAnswer, RoomState } from "@/shared/api/types";
import type { ServerClock } from "@/shared/realtime";
import { Button } from "@/shared/ui/button";
import { clueRegistry, clueSceneOf } from "@/features/game/clues/registry";
import { useModeTitle } from "@/features/online/lists";
import { isKnownAnswerFormat, matchAnswerRegistry } from "./answers/registry";
import { ChatPanel } from "./ChatPanel";
import { PhaseTimer, useRemainingSeconds } from "./PhaseTimer";
import { PlayersRail } from "./PlayersRail";
import { canAnswer, type FeedItem, type RoomStore } from "./room-store";
import { RoundRevealPanel } from "./RoundRevealPanel";
import { useLevelClue } from "./use-level-clue";

export type MatchCommands = {
  guess: (answer: MatchAnswer) => Promise<unknown>;
  skip: () => Promise<unknown>;
  forfeit: () => Promise<unknown>;
  /** «Отошёл» / «Вернулся» (`POST /match/away`): соперники видят отметку у места. */
  away: (away: boolean) => Promise<unknown>;
  emote: (kind: "emote" | "phrase", key: string) => Promise<unknown>;
  chat: (text: string) => Promise<unknown>;
};

type Props = {
  code: string;
  state: RoomState;
  store: RoomStore;
  clock: ServerClock;
  myId: string | null;
  bubbles: Map<string, FeedItem>;
  act: (action: () => Promise<unknown>) => Promise<void>;
  commands: MatchCommands;
};

export function MatchStage({ code, state, store, clock, myId, bubbles, act, commands }: Props) {
  const t = useTranslations();
  const modeTitle = useModeTitle();
  const { room, match, me } = state;
  const domain = room.mode.domain;
  const [busy, setBusy] = useState(false);
  const [confirmForfeit, setConfirmForfeit] = useState(false);
  const clue = useLevelClue(code, room.phase === "level_open" ? match?.clue : null);
  const Clue = clue.data ? clueRegistry[clue.data.kind] : null;
  const format = room.settings.answer_format;
  const Answer = isKnownAnswerFormat(format) ? matchAnswerRegistry[format] : null;
  const level = match?.level ?? null;
  const myOpen = canAnswer(store, myId);
  const mySlot = room.players.find((p) => p.user_id === myId && p.presence !== "left") ?? null;
  const iAmIn = mySlot !== null;

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await act(action);
      } finally {
        setBusy(false);
      }
    },
    [act],
  );
  const onGuess = useCallback(
    (answer: MatchAnswer) => void run(() => commands.guess(answer)),
    [run, commands],
  );
  const onSkip = useCallback(() => void run(commands.skip), [run, commands]);

  return (
    <div
      className="flex flex-col gap-6"
      data-domain={domain}
      data-testid="match-stage"
      data-phase={room.phase}
    >
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-[var(--accent)]">{modeTitle(room.mode)}</p>
          {match ? (
            <h1 className="font-display text-2xl font-bold" data-testid="match-round">
              {t("game.roundTitle", { index: match.round_index + 1, total: match.rounds_total })}
            </h1>
          ) : (
            <h1 className="font-display text-2xl font-bold">{t("match.starting")}</h1>
          )}
          {match && level !== null && match.levels_count > 1 && (
            <p className="text-sm text-ink-muted" data-testid="match-level">
              {t("match.levelOf", { level, total: match.levels_count })}
            </p>
          )}
        </div>
        {(room.phase === "level_open" || room.phase === "reveal") && (
          <PhaseTimer
            clock={clock}
            endsAt={room.phase_ends_at}
            totalS={room.phase === "level_open" ? room.settings.level_window_s : null}
            className="w-full sm:w-72"
          />
        )}
      </header>

      <PlayersRail
        players={room.players}
        matchPlayers={match?.players}
        myId={myId}
        bubbles={bubbles}
      />

      {room.phase === "countdown" && <Countdown clock={clock} endsAt={room.phase_ends_at} />}

      {room.phase === "paused" && (
        <div
          role="status"
          className="rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule"
          data-testid="match-paused"
        >
          <p className="font-display text-xl font-semibold">{t("match.paused.title")}</p>
          <p className="text-ink-muted">{t("match.paused.text")}</p>
          <PhaseTimer clock={clock} endsAt={room.phase_ends_at} className="mt-3" />
        </div>
      )}

      {(room.phase === "level_open" || room.phase === "level_closed") && match && (
        <>
          <section
            aria-label={t("game.clueLabel")}
            className="rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
          >
            <div className="min-h-44">
              {room.phase === "level_closed" ? (
                <div
                  className="grid min-h-44 place-items-center text-ink-muted"
                  role="status"
                  data-testid="level-closed"
                >
                  {t("match.levelClosed")}
                </div>
              ) : Clue && clue.data ? (
                <Clue clue={clue.data} scene={clueSceneOf(room.settings.clue_type)} />
              ) : (
                <div className="grid min-h-44 place-items-center text-ink-muted" role="status">
                  {clue.isError || match.clue === null
                    ? t("game.clueFailed")
                    : t("game.clueLoading")}
                </div>
              )}
            </div>
          </section>

          {me && me.round_index === match.round_index && me.guesses.length > 0 && (
            <ol
              className="flex flex-wrap gap-2 text-sm"
              aria-label={t("game.historyLabel")}
              data-testid="my-guesses"
            >
              {me.guesses.map((g, i) => (
                <li
                  key={i}
                  className="rounded-full bg-stage-raised px-3 py-1 ring-1 ring-rule"
                  data-result={g.result ?? "skip"}
                >
                  <span dir="auto">{g.kind === "skip" ? t("game.skipped") : g.display}</span>
                  <span className="text-ink-faint"> · {t(`game.guess.${g.result ?? "skip"}`)}</span>
                </li>
              ))}
            </ol>
          )}

          {iAmIn &&
            (Answer ? (
              <Answer
                domain={domain}
                me={me ?? null}
                disabled={!myOpen || busy}
                canSkip={level !== null && level < match.levels_count}
                onGuess={onGuess}
                onSkip={onSkip}
                focusKey={`${match.round_index}:${level}`}
              />
            ) : (
              <p className="text-ink-muted">{t("match.formatUnsupported")}</p>
            ))}
          {me?.status === "guessed" && (
            <p className="text-exact" role="status" data-testid="me-guessed">
              {t("match.youGuessed")}
            </p>
          )}
        </>
      )}

      {room.phase === "reveal" && store.reveal && (
        <RoundRevealPanel
          code={code}
          reveal={store.reveal}
          players={room.players}
          domain={domain}
        />
      )}

      <ChatPanel
        chat={room.chat}
        feed={store.feed}
        players={room.players}
        onEmote={commands.emote}
        onChat={commands.chat}
      />

      {iAmIn && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={mySlot.away}
            onClick={() => void run(() => commands.away(!mySlot.away))}
            data-testid="away-toggle"
          >
            <Coffee aria-hidden />
            {mySlot.away ? t("match.awayBack") : t("match.awaySet")}
          </Button>
          {confirmForfeit ? (
            <>
              <span className="text-sm text-ink-muted">
                {room.mode.kind === "duel"
                  ? t("match.forfeit.confirmDuel")
                  : t("match.forfeit.confirmParty")}
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void run(commands.forfeit).then(() => setConfirmForfeit(false))}
                data-testid="forfeit-confirm"
              >
                {t("match.forfeit.yes")}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmForfeit(false)}>
                {t("match.forfeit.no")}
              </Button>
            </>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmForfeit(true)}
              data-testid="forfeit"
            >
              <Flag aria-hidden />
              {t("match.forfeit.action")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function Countdown({ clock, endsAt }: { clock: ServerClock; endsAt: string | null | undefined }) {
  const t = useTranslations();
  const seconds = useRemainingSeconds(clock, endsAt);
  return (
    <div
      className="grid place-items-center gap-2 rounded-[var(--radius-ticket)] bg-stage py-12 ring-1 ring-rule"
      role="status"
      data-testid="countdown"
    >
      <p className="text-ink-muted">{t("match.countdown")}</p>
      <p className="tabular font-display text-6xl font-bold text-[var(--accent)]">
        {seconds ?? "…"}
      </p>
    </div>
  );
}
