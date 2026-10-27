"use client";
/**
 * Игровой экран (CSR, D2): номер раунда, прогресс сессии (жизни, серия, тур сетки), строки
 * попыток, лента уровней, загадка уровня, ответ и модалка ответа раунда.
 *
 * Подача выбирается по данным (D12 плана этапа 3a): загадка — по `clue.kind` (сцена — по
 * `clue_type`), ответ — по `answer_format` режима, ревил — по `presentation`. Ключ режима и
 * `source_type` экран не читает. Режим сессии ищется по `mode.key` снимка во всех видах каталога
 * (`kind=play` и `kind=collection`), поэтому у сессии по сборнику те же уровни и название.
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useFormatter, useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { AnswerFormat, ModeOut, Presentation, RoundOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { SectionCap } from "@/shared/ui/section-cap";
import { GameShellSlots } from "@/features/shell/game-shell-slots";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { AttemptRows } from "./AttemptRows";
import { ExitLink } from "./ExitLink";
import { MuteButton } from "./MuteButton";
import { RoundTabs } from "./RoundTabs";
import { RevealDialog } from "./RevealDialog";
import { SessionProgress } from "./SessionProgress";
import { needsLevelClue } from "./answers/pick";
import { answerRegistry } from "./answers/registry";
import { clueRegistry, clueSceneOf } from "./clues/registry";
import { TapeDeck } from "./TapeDeck";
import { tapeWeights } from "./levels";
import { useSessionMode } from "./modes";
import type { Move } from "./moves";
import { readyForResults, statsOf, visibleRound } from "./state";
import { useClue, useReleaseClues } from "./use-clue";
import { useGame } from "./use-game";

/** Формат ответа: из режима, а пока список режимов не пришёл — по наличию вариантов раунда. */
export function answerFormatOf(mode: ModeOut | undefined, round: RoundOut): AnswerFormat {
  return mode?.answer_format ?? ((round.options ?? []).length > 0 ? "pick" : "classic");
}

export function GameScreen({ sessionId }: { sessionId: string }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const format = useFormatter();
  const router = useRouter();
  const toast = useToast();
  const { state, error, act, proceed, dismissNotice, reportError } = useGame(sessionId);
  useReleaseClues(sessionId);
  const snapshot = state.snapshot;
  const round = visibleRound(state);
  const revealing = state.revealRound !== null;
  const playable =
    !!snapshot && snapshot.status === "active" && round?.status === "active" && !revealing;
  const { mode } = useSessionMode(snapshot?.mode.domain, snapshot?.mode.key);
  const levels = mode?.levels ?? [];
  const withClue = !!round && needsLevelClue(round);
  const clue = useClue(
    sessionId,
    playable && withClue ? round.index : null,
    playable && withClue ? round.level : null,
  );
  // Доля воспроизведения привязана к уровню: смена раунда или уровня сбрасывает бегунок.
  const levelTag = `${round?.index ?? -1}:${round?.level ?? -1}`;
  const [progressState, setProgressState] = useState<{ tag: string; value: number | null }>({
    tag: levelTag,
    value: null,
  });
  const progress = progressState.tag === levelTag ? progressState.value : null;
  const setProgress = useCallback(
    (value: number | null) => setProgressState({ tag: levelTag, value }),
    [levelTag],
  );
  // Кадровая загадка показывает сам кадр вместо ленты; вид помним и на ответе раунда.
  const [imageClue, setImageClue] = useState(false);
  const knownKind = clue.data?.kind;
  if (knownKind && imageClue !== (knownKind === "image")) setImageClue(knownKind === "image");
  const onMove = useCallback((move: Move) => void act(move), [act]);

  // Уведомления хода — тостом.
  useEffect(() => {
    if (!state.notice) return;
    toast(t(`game.notice.${state.notice}`));
    dismissNotice();
  }, [state.notice, toast, t, dismissNotice]);

  // Ошибка выдачи ассета (410, 403, исчерпаны повторы) — как ошибка хода: ресинк и уведомление.
  const clueError = clue.error;
  useEffect(() => {
    if (clueError) reportError(clueError);
  }, [clueError, reportError]);

  useEffect(() => {
    if (readyForResults(state)) router.replace(`/results/${sessionId}`);
  }, [state, router, sessionId]);

  if (error) {
    const missing = isApiError(error) && error.status === 404;
    return (
      <div className="flex flex-col items-start gap-4 pt-10">
        <h1 className="font-display text-2xl font-bold">
          {t(missing ? "game.notFound" : "game.loadFailed")}
        </h1>
        <Button variant="secondary" onClick={() => router.push("/")}>
          {t("common.home")}
        </Button>
      </div>
    );
  }

  if (!snapshot || !round) {
    return (
      <p className="pt-10 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }

  const domain = snapshot.mode.domain;
  const presentation: Presentation = mode?.presentation ?? "score";
  const weights = tapeWeights(levels, round.levels_count);
  const Clue = clue.data ? clueRegistry[clue.data.kind] : null;
  const Answer = answerRegistry[answerFormatOf(mode, round)];
  const isLastRound = round.index === snapshot.rounds.length - 1 || snapshot.status === "finished";
  const multiLevel = round.levels_count > 1;
  const modeTitle = dt(mode?.title_key ?? "", t(`domain.${domain}`));
  const day = snapshot.day
    ? format.dateTime(new Date(`${snapshot.day}T12:00:00`), { day: "numeric", month: "long" })
    : null;
  const modeLabel = [modeTitle, t(`domain.${domain}`).toLowerCase(), day]
    .filter(Boolean)
    .join(" · ");
  const showTabs =
    presentation === "score" && snapshot.rounds.length > 1 && snapshot.rounds.length <= 10;
  const tapeProgress =
    progress === null || !weights.length
      ? null
      : (weights.slice(0, round.level).reduce((a, b) => a + b, 0) * progress) /
        weights.reduce((a, b) => a + b, 0);

  return (
    <div data-domain={domain} className="flex flex-col gap-[26px] pt-2">
      <GameShellSlots
        exit={<ExitLink />}
        volume={<MuteButton />}
        modeLabel={modeLabel}
        score={
          mode?.presentation === "score" ? (
            <p className="flex flex-col items-end gap-1" data-testid="score">
              <span className="inline-flex items-baseline gap-1.5">
                <span className="tabular font-display text-[28px] leading-none font-extrabold tracking-tight">
                  <span className="sr-only">{t("game.score")}: </span>
                  {snapshot.score}
                </span>
                <span className="tabular font-display text-[15px] font-medium text-ink-faint">
                  / {snapshot.max_score}
                </span>
              </span>
              <SectionCap>{t("game.scoreCap")}</SectionCap>
            </p>
          ) : null
        }
      />

      <h1
        className="sr-only"
        aria-label={t("game.roundTitle", { index: round.index + 1, total: snapshot.rounds.length })}
        data-testid="round-title"
      >
        {t("game.roundTitle", { index: round.index + 1, total: snapshot.rounds.length })}
      </h1>

      <div className="flex flex-col gap-2.5">
        {showTabs && <RoundTabs rounds={snapshot.rounds} current={round.index} />}
        <SessionProgress rounds={snapshot.rounds} round={round} />
        {multiLevel && (
          <AttemptRows guesses={round.guesses} levelsCount={round.levels_count} active={playable} />
        )}
      </div>

      {multiLevel && (withClue || revealing) && !imageClue && (
        <TapeDeck weights={weights} levels={levels} level={round.level} progress={tapeProgress} />
      )}

      {revealing ? (
        <RevealDialog
          sessionId={sessionId}
          round={round}
          presentation={presentation}
          stats={statsOf(state, round.index)}
          last={isLastRound}
          onNext={proceed}
          domain={domain}
        />
      ) : (
        <>
          {withClue && (
            <section aria-label={t("game.clueLabel")} className="flex flex-col items-center">
              {Clue && clue.data ? (
                <Clue
                  clue={clue.data}
                  scene={clueSceneOf(snapshot.mode.clue_type)}
                  onProgress={setProgress}
                />
              ) : (
                <div className="grid min-h-[88px] place-items-center text-ink-muted" role="status">
                  {clue.isError ? t("game.clueFailed") : t("game.clueLoading")}
                </div>
              )}
            </section>
          )}

          <Answer
            round={round}
            domain={domain}
            levels={levels}
            disabled={!playable || state.busy}
            onMove={onMove}
            focusKey={`${round.index}:${round.level}`}
          />
        </>
      )}
    </div>
  );
}
