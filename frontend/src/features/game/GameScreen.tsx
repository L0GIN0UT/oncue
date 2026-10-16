"use client";
/**
 * Игровой экран (CSR, D2): индикатор раундов, прогресс сессии (жизни, серия, тур сетки),
 * загадка уровня, лента уровней, история попыток, ответ и ревил после раунда.
 *
 * Подача выбирается по данным (D12 плана этапа 3a): загадка — по `clue.kind` (сцена — по
 * `clue_type`), ответ — по `answer_format` режима, ревил — по `presentation`. Ключ режима и
 * `source_type` экран не читает.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { AnswerFormat, Domain, ModeOut, Presentation, RoundOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { AttemptHistory } from "./AttemptHistory";
import { LevelTape } from "./LevelTape";
import { RevealCard } from "./RevealCard";
import { RoundRail } from "./RoundRail";
import { SessionProgress } from "./SessionProgress";
import { needsLevelClue } from "./answers/pick";
import { answerRegistry } from "./answers/registry";
import { clueRegistry, clueSceneOf } from "./clues/registry";
import { tapeWeights } from "./levels";
import type { Move } from "./moves";
import { readyForResults, statsOf, visibleRound } from "./state";
import { useClue, useReleaseClues } from "./use-clue";
import { useGame } from "./use-game";

/** Индикатор точками — пока раундов немного; длинные сессии (марафон, сетка) — только текстом. */
const RAIL_MAX_ROUNDS = 12;

function useModes(domain: Domain | undefined) {
  return useQuery({
    queryKey: ["modes", domain],
    queryFn: async (): Promise<ModeOut[]> =>
      unwrap(await api().game.GET("/v1/modes", { params: { query: { domain } } })),
    enabled: !!domain,
    staleTime: 5 * 60_000,
  });
}

/** Формат ответа: из режима, а пока список режимов не пришёл — по наличию вариантов раунда. */
export function answerFormatOf(mode: ModeOut | undefined, round: RoundOut): AnswerFormat {
  return mode?.answer_format ?? ((round.options ?? []).length > 0 ? "pick" : "classic");
}

export function GameScreen({ sessionId }: { sessionId: string }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const toast = useToast();
  const { state, error, act, proceed, dismissNotice, reportError } = useGame(sessionId);
  useReleaseClues(sessionId);
  const snapshot = state.snapshot;
  const round = visibleRound(state);
  const revealing = state.revealRound !== null;
  const playable =
    !!snapshot && snapshot.status === "active" && round?.status === "active" && !revealing;
  const modes = useModes(snapshot?.mode.domain);
  const mode = modes.data?.find((m) => m.key === snapshot?.mode.key);
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
  const inputRef = useRef<HTMLInputElement>(null);
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

  useEffect(() => {
    if (playable) inputRef.current?.focus({ preventScroll: true });
  }, [playable, round?.index, round?.level]);

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
  const tapeProgress =
    progress === null || !weights.length
      ? null
      : (weights.slice(0, round.level).reduce((a, b) => a + b, 0) * progress) /
        weights.reduce((a, b) => a + b, 0);

  return (
    <div data-domain={domain} className="flex flex-col gap-6 pt-4 sm:pt-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-[var(--accent)]">
            {dt(mode?.title_key ?? "", t(`domain.${domain}`))}
          </p>
          <h1 className="font-display text-2xl font-bold" data-testid="round-title">
            {t("game.roundTitle", { index: round.index + 1, total: snapshot.rounds.length })}
          </h1>
        </div>
        <div className="flex items-center gap-5">
          {snapshot.rounds.length <= RAIL_MAX_ROUNDS && (
            <RoundRail rounds={snapshot.rounds} current={round.index} />
          )}
          {mode?.presentation === "score" && (
            <p className="tabular font-display text-xl font-semibold" data-testid="score">
              <span className="sr-only">{t("game.score")}: </span>
              {snapshot.score}
              <span className="text-ink-faint">/{snapshot.max_score}</span>
            </p>
          )}
        </div>
      </header>

      <SessionProgress rounds={snapshot.rounds} round={round} />

      {revealing ? (
        <RevealCard
          sessionId={sessionId}
          round={round}
          presentation={presentation}
          stats={statsOf(state, round.index)}
          last={isLastRound}
          onNext={proceed}
        />
      ) : (
        <>
          {withClue && (
            <section
              aria-label={t("game.clueLabel")}
              className="flex flex-col gap-5 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
            >
              <div className="min-h-44">
                {Clue && clue.data ? (
                  <Clue
                    clue={clue.data}
                    scene={clueSceneOf(snapshot.mode.clue_type)}
                    onProgress={setProgress}
                  />
                ) : (
                  <div className="grid min-h-44 place-items-center text-ink-muted" role="status">
                    {clue.isError ? t("game.clueFailed") : t("game.clueLoading")}
                  </div>
                )}
              </div>
              {multiLevel && (
                <LevelTape
                  weights={weights}
                  level={round.level}
                  guesses={round.guesses}
                  progress={tapeProgress}
                />
              )}
            </section>
          )}

          {multiLevel && (
            <AttemptHistory guesses={round.guesses} levelsCount={round.levels_count} />
          )}

          <Answer
            round={round}
            domain={domain}
            levels={levels}
            disabled={!playable || state.busy}
            onMove={onMove}
            inputRef={inputRef}
          />
        </>
      )}
    </div>
  );
}
