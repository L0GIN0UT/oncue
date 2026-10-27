"use client";
/**
 * Экран матча (макеты Duel и A-Game): у дуэли — полоса счёта сверху, у компании — места игроков;
 * дальше тот же игровой экран, что в соло: строки попыток, лента уровней, загадка (реестр
 * `clueRegistry`, сцена — по `clue_type`), ответ (реестр форматов матча по `answer_format`),
 * таймер по `phase_ends_at`, эмоции и чат, ответ раунда. Уровень приходит только после открытия
 * (`level_opened`), исход своего ответа — только в ответе команды; время и очки считает сервер.
 * «Сдаться»/«Выйти» стоит в левом слоте шапки.
 */
import { useCallback, useState } from "react";
import { useTranslations } from "next-intl";
import { Coffee, LogOut } from "lucide-react";
import type { GuessOut, MatchAnswer, RoomState } from "@/shared/api/types";
import type { ServerClock } from "@/shared/realtime";
import { Button } from "@/shared/ui/button";
import { Modal } from "@/shared/ui/modal";
import { AttemptRows } from "@/features/game/AttemptRows";
import { TapeDeck } from "@/features/game/TapeDeck";
import { clueRegistry, clueSceneOf } from "@/features/game/clues/registry";
import { tapeWeights } from "@/features/game/levels";
import { useSessionMode } from "@/features/game/modes";
import { MuteButton } from "@/features/game/MuteButton";
import { GameShellSlots } from "@/features/shell/game-shell-slots";
import { useModeTitle } from "@/features/online/lists";
import { isKnownAnswerFormat, matchAnswerRegistry } from "./answers/registry";
import { ChatPanel } from "./ChatPanel";
import { PhaseTimer, useRemainingSeconds } from "./PhaseTimer";
import { PlayersRail } from "./PlayersRail";
import { canAnswer, type FeedItem, type RoomStore } from "./room-store";
import { RoundRevealPanel } from "./RoundRevealPanel";
import { ScoreStrip, useRoundWinners } from "./ScoreStrip";
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
  const duel = room.mode.kind === "duel";
  const [busy, setBusy] = useState(false);
  const [confirmForfeit, setConfirmForfeit] = useState(false);
  const [progress, setProgress] = useState<{ tag: string; value: number | null } | null>(null);
  const clue = useLevelClue(code, room.phase === "level_open" ? match?.clue : null);
  const Clue = clue.data ? clueRegistry[clue.data.kind] : null;
  const format = room.settings.answer_format;
  const Answer = isKnownAnswerFormat(format) ? matchAnswerRegistry[format] : null;
  const level = match?.level ?? null;
  const myOpen = canAnswer(store, myId);
  const mySlot = room.players.find((p) => p.user_id === myId && p.presence !== "left") ?? null;
  const iAmIn = mySlot !== null;
  const winners = useRoundWinners(store.reveal);
  const { mode } = useSessionMode(domain, room.mode.key);
  const levels = mode?.levels ?? [];
  const weights = tapeWeights(levels, match?.levels_count ?? 1);
  const rivalStatus = duel ? (match?.players.find((p) => p.user_id !== myId) ?? null) : null;
  const rival = duel ? (room.players.find((p) => p.user_id !== myId) ?? null) : null;

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

  const levelOpen = (room.phase === "level_open" || room.phase === "level_closed") && match;
  const roundTag = `${match?.round_index}:${level}`;
  const playing = progress?.tag === roundTag ? progress.value : null;
  const guesses: GuessOut[] = me && match && me.round_index === match.round_index ? me.guesses : [];
  const rowsTotal = guesses.length + (me?.attempts_left ?? 0);
  const tapeProgress =
    playing === null || !weights.length
      ? null
      : (weights.slice(0, level ?? 1).reduce((a, b) => a + b, 0) * playing) /
        weights.reduce((a, b) => a + b, 0);

  return (
    <div
      className="flex flex-col gap-6"
      data-domain={domain}
      data-testid="match-stage"
      data-phase={room.phase}
    >
      <GameShellSlots
        exit={
          iAmIn ? (
            <button
              type="button"
              onClick={() => setConfirmForfeit(true)}
              data-testid="forfeit"
              className="inline-flex size-11 items-center justify-center gap-2 rounded-full border-[1.5px] border-rule bg-stage text-sm font-semibold text-ink transition-colors hover:border-rule-strong sm:w-auto sm:pr-4 sm:pl-3"
              aria-label={duel ? t("match.forfeit.actionDuel") : t("match.forfeit.actionParty")}
            >
              <LogOut className="size-[18px]" aria-hidden />
              <span className="hidden sm:inline">
                {duel ? t("match.forfeit.actionDuel") : t("match.forfeit.actionParty")}
              </span>
            </button>
          ) : undefined
        }
        volume={<MuteButton />}
        modeLabel={modeTitle(room.mode)}
      />

      {/* Номер раунда для скринридера и тестов; на экране его показывают полоса счёта и крупная цифра. */}
      {match ? (
        <h1 className="sr-only" data-testid="match-round">
          {t("game.roundTitle", { index: match.round_index + 1, total: match.rounds_total })}
        </h1>
      ) : (
        <h1 className="font-display text-2xl font-bold">{t("match.starting")}</h1>
      )}

      <div className="flex flex-col gap-2.5">
        {duel && match ? (
          <ScoreStrip
            players={room.players}
            matchPlayers={match.players}
            myId={myId}
            roundIndex={match.round_index}
            roundsTotal={match.rounds_total}
            winners={winners}
            bubbles={bubbles}
          />
        ) : (
          <>
            {match && (
              <div className="flex items-end justify-between gap-3 px-0.5" aria-hidden>
                <span className="flex items-baseline gap-2.5">
                  <span className="font-display text-[40px] leading-none font-extrabold tracking-tight">
                    {match.round_index + 1}
                  </span>
                  <span className="font-display text-[15px] font-medium text-ink-muted">
                    {t("game.roundOf", { total: match.rounds_total })}
                  </span>
                </span>
                <span className="text-sm text-ink-muted">{modeTitle(room.mode)}</span>
              </div>
            )}
            <PlayersRail
              players={room.players}
              matchPlayers={match?.players}
              myId={myId}
              bubbles={bubbles}
            />
          </>
        )}
        {levelOpen && rowsTotal > 0 && (
          <AttemptRows
            guesses={guesses}
            levelsCount={rowsTotal}
            active={myOpen}
            testId={guesses.length > 0 ? "my-guesses" : "my-attempts"}
          />
        )}
      </div>

      {match && match.levels_count > 1 && level !== null && (
        <p className="sr-only" data-testid="match-level">
          {t("match.levelOf", { level, total: match.levels_count })}
        </p>
      )}

      {(room.phase === "level_open" || room.phase === "reveal") && (
        <PhaseTimer
          clock={clock}
          endsAt={room.phase_ends_at}
          totalS={room.phase === "level_open" ? room.settings.level_window_s : null}
        />
      )}

      {room.phase === "countdown" && <Countdown clock={clock} endsAt={room.phase_ends_at} />}

      {room.phase === "paused" && (
        <div
          role="status"
          className="rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-5"
          data-testid="match-paused"
        >
          <p className="font-display text-lg font-semibold">{t("match.paused.title")}</p>
          <p className="text-ink-muted">{t("match.paused.text")}</p>
          <PhaseTimer clock={clock} endsAt={room.phase_ends_at} className="mt-3" />
        </div>
      )}

      {levelOpen && (
        <>
          {match.levels_count > 1 && level !== null && (
            <TapeDeck
              weights={weights}
              levels={levels}
              level={level}
              progress={room.phase === "level_open" ? tapeProgress : null}
            />
          )}

          {room.phase === "level_open" && rivalStatus?.status && rival && (
            <p
              className="flex items-center justify-end gap-2 text-[13px] text-ink-muted"
              data-testid="rival-line"
            >
              <span
                aria-hidden
                className="size-2 animate-pulse rounded-full bg-music motion-reduce:animate-none"
              />
              {t(`match.rival.${rivalStatus.status}`, { name: rival.nickname })}
            </p>
          )}

          <section aria-label={t("game.clueLabel")} className="flex flex-col items-center">
            {room.phase === "level_closed" ? (
              <div
                className="grid min-h-[88px] place-items-center text-ink-muted"
                role="status"
                data-testid="level-closed"
              >
                {t("match.levelClosed")}
              </div>
            ) : Clue && clue.data ? (
              <Clue
                clue={clue.data}
                scene={clueSceneOf(room.settings.clue_type)}
                onProgress={(value) => setProgress({ tag: roundTag, value })}
              />
            ) : (
              <div className="grid min-h-[88px] place-items-center text-ink-muted" role="status">
                {clue.isError || match.clue === null ? t("game.clueFailed") : t("game.clueLoading")}
              </div>
            )}
          </section>

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
            <p className="text-center text-exact" role="status" data-testid="me-guessed">
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
        <div className="flex justify-center">
          <Button
            variant="quiet"
            size="sm"
            aria-pressed={mySlot.away}
            onClick={() => void run(() => commands.away(!mySlot.away))}
            data-testid="away-toggle"
          >
            <Coffee aria-hidden />
            {mySlot.away ? t("match.awayBack") : t("match.awaySet")}
          </Button>
        </div>
      )}

      <Modal
        open={confirmForfeit}
        onClose={() => setConfirmForfeit(false)}
        aria-label={duel ? t("match.forfeit.actionDuel") : t("match.forfeit.actionParty")}
        className="max-w-[400px]"
      >
        <div className="flex flex-col gap-5 p-5">
          <p className="text-base">
            {duel ? t("match.forfeit.confirmDuel") : t("match.forfeit.confirmParty")}
          </p>
          <div className="flex gap-2.5">
            <Button
              variant="secondary"
              className="flex-1"
              onClick={() => setConfirmForfeit(false)}
              data-testid="forfeit-cancel"
            >
              {t("match.forfeit.no")}
            </Button>
            <Button
              className="flex-1"
              onClick={() => void run(commands.forfeit).then(() => setConfirmForfeit(false))}
              data-testid="forfeit-confirm"
            >
              {t("match.forfeit.yes")}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Countdown({ clock, endsAt }: { clock: ServerClock; endsAt: string | null | undefined }) {
  const t = useTranslations();
  const seconds = useRemainingSeconds(clock, endsAt);
  return (
    <div
      className="grid place-items-center gap-2 rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage py-12"
      role="status"
      data-testid="countdown"
    >
      <p className="text-ink-muted">{t("match.countdown")}</p>
      <p className="tabular font-display text-6xl font-extrabold text-music">{seconds ?? "…"}</p>
    </div>
  );
}
