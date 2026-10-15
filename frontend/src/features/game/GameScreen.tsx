"use client";
/**
 * Игровой экран (CSR, D2): индикатор раундов, загадка уровня, лента уровней, история попыток,
 * поле ответа с автокомплитом, «Пропустить» и «Сдаться», ревил после раунда.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { Flag, SkipForward } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { Domain, ModeOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { AnswerBox } from "./AnswerBox";
import { AttemptHistory } from "./AttemptHistory";
import { LevelTape } from "./LevelTape";
import { RevealCard } from "./RevealCard";
import { RoundRail } from "./RoundRail";
import { clueRegistry } from "./clues/registry";
import { skipGainSeconds, tapeWeights } from "./levels";
import { readyForResults, visibleRound } from "./state";
import { useClue, useReleaseClues } from "./use-clue";
import { useGame } from "./use-game";

function useModes(domain: Domain | undefined) {
  return useQuery({
    queryKey: ["modes", domain],
    queryFn: async (): Promise<ModeOut[]> =>
      unwrap(await api().game.GET("/v1/modes", { params: { query: { domain } } })),
    enabled: !!domain,
    staleTime: 5 * 60_000,
  });
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
  const modes = useModes(snapshot?.mode.domain);
  const mode = modes.data?.find((m) => m.key === snapshot?.mode.key);
  const levels = mode?.levels ?? [];
  const clue = useClue(sessionId, playable ? round.index : null, playable ? round.level : null);
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
  const weights = tapeWeights(levels, round.levels_count);
  const gain = skipGainSeconds(levels, round.level);
  const lastLevel = round.level >= round.levels_count;
  const Clue = clue.data ? (clueRegistry[clue.data.kind] ?? null) : null;
  const isLastRound = round.index === snapshot.rounds.length - 1 || snapshot.status === "finished";
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
          <RoundRail rounds={snapshot.rounds} current={round.index} />
          <p className="tabular font-display text-xl font-semibold" data-testid="score">
            <span className="sr-only">{t("game.score")}: </span>
            {snapshot.score}
            <span className="text-ink-faint">/{snapshot.max_score}</span>
          </p>
        </div>
      </header>

      {revealing ? (
        <RevealCard round={round} last={isLastRound} onNext={proceed} />
      ) : (
        <>
          <section
            aria-label={t("game.clueLabel")}
            className="flex flex-col gap-5 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
          >
            <div className="min-h-44">
              {Clue && clue.data ? (
                <Clue clue={clue.data} onProgress={setProgress} />
              ) : (
                <div className="grid min-h-44 place-items-center text-ink-muted" role="status">
                  {clue.isError ? t("game.clueFailed") : t("game.clueLoading")}
                </div>
              )}
            </div>
            <LevelTape
              weights={weights}
              level={round.level}
              guesses={round.guesses}
              progress={tapeProgress}
            />
          </section>

          <AttemptHistory guesses={round.guesses} levelsCount={round.levels_count} />

          <AnswerBox
            domain={domain}
            disabled={!playable || state.busy}
            inputRef={inputRef}
            onAnswer={(answer) => void act({ kind: "guess", answer })}
          />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Button
              variant="secondary"
              disabled={!playable || state.busy}
              onClick={() => void act({ kind: "skip" })}
              data-testid="skip"
            >
              <SkipForward aria-hidden />
              {lastLevel
                ? t("game.skipLast")
                : gain !== null
                  ? t("game.skipSeconds", {
                      seconds: format.number(gain, { maximumFractionDigits: 1 }),
                    })
                  : t("game.skip")}
            </Button>
            <Button
              variant="ghost"
              disabled={!playable || state.busy}
              onClick={() => void act({ kind: "give_up" })}
              data-testid="give-up"
            >
              <Flag aria-hidden />
              {t("game.giveUp")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
