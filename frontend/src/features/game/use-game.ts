/**
 * Игровая сессия на клиенте: снимок (`GET /v1/sessions/{id}`), ходы и обработка ошибок
 * по плану §7: 409 `game.desync` → снимок из `details`; 409 `game.round_finished` → ресинк;
 * 410 → «раунд не засчитан»; 429 → уведомление; сеть → ресинк.
 */
import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { api, unwrap } from "@/shared/api/client";
import { ApiError, NetworkError } from "@/shared/api/errors";
import type { SessionSnapshot } from "@/shared/api/types";
import { sendMove, type Move } from "./moves";
import { gameReducer, initialGameState, type GameAction, type NoticeKind } from "./state";

export const sessionKey = (id: string) => ["game-session", id] as const;

const isSnapshot = (v: unknown): v is SessionSnapshot =>
  !!v && typeof v === "object" && "rounds" in v && "version" in v && "id" in v;

/** Снимок из `details` ответа 409 `game.desync` (`details.session`, `details.snapshot` или сам `details`). */
export function snapshotFromDetails(
  details: Record<string, unknown> | null,
): SessionSnapshot | null {
  if (!details) return null;
  for (const candidate of [details.session, details.snapshot, details]) {
    if (isSnapshot(candidate)) return candidate;
  }
  return null;
}

/** Реакция на ошибку хода или выдачи ассета: что показать и нужен ли ресинк. */
export function classifyError(e: unknown): {
  notice: NoticeKind | null;
  resync: boolean;
  snapshot?: SessionSnapshot;
} {
  if (e instanceof NetworkError) return { notice: "network", resync: true };
  if (!(e instanceof ApiError)) return { notice: "failed", resync: true };
  if (e.code === "game.desync") {
    const snapshot = snapshotFromDetails(e.details);
    return snapshot
      ? { notice: "desync", resync: false, snapshot }
      : { notice: "desync", resync: true };
  }
  if (e.code === "game.round_finished" || e.code === "game.level_locked")
    return { notice: null, resync: true };
  if (e.status === 410) return { notice: "round_void", resync: true };
  if (e.status === 429) return { notice: "rate_limited", resync: false };
  return { notice: "failed", resync: true };
}

export async function fetchSnapshot(sessionId: string): Promise<SessionSnapshot> {
  return unwrap<SessionSnapshot>(
    await api().game.GET("/v1/sessions/{session_id}", {
      params: { path: { session_id: sessionId } },
    }),
  );
}

export function useGame(sessionId: string) {
  const [state, dispatchRaw] = useReducer(gameReducer, initialGameState);
  const stateRef = useRef(state);
  const dispatch = useCallback((action: GameAction) => {
    stateRef.current = gameReducer(stateRef.current, action);
    dispatchRaw(action);
  }, []);

  const query = useQuery({
    queryKey: sessionKey(sessionId),
    queryFn: () => fetchSnapshot(sessionId),
    refetchOnMount: "always",
  });
  const { refetch } = query;

  useEffect(() => {
    if (query.data) dispatch({ type: "snapshot", snapshot: query.data });
  }, [query.data, dispatch]);

  const handleError = useCallback(
    (e: unknown) => {
      const { notice, resync, snapshot } = classifyError(e);
      if (snapshot) dispatch({ type: "snapshot", snapshot });
      dispatch({ type: "move_failed", notice });
      if (resync) void refetch();
    },
    [dispatch, refetch],
  );

  const act = useCallback(
    async (move: Move) => {
      const current = stateRef.current;
      const s = current.snapshot;
      if (!s || current.busy || s.status !== "active" || current.revealRound !== null) return;
      dispatch({ type: "move_started" });
      try {
        const result = await sendMove(
          { sessionId, roundIndex: s.current_round, expectedVersion: s.version },
          move,
        );
        dispatch({ type: "move_succeeded", result });
      } catch (e) {
        handleError(e);
      }
    },
    [dispatch, handleError, sessionId],
  );

  return {
    state,
    error: query.error,
    isLoading: query.isPending,
    act,
    proceed: useCallback(() => dispatch({ type: "continue" }), [dispatch]),
    dismissNotice: useCallback(() => dispatch({ type: "dismiss_notice" }), [dispatch]),
    reportError: handleError,
  };
}
