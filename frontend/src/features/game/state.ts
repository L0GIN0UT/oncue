/**
 * Состояние игрового экрана. Источник истины — снимок сессии с сервера (`SessionSnapshot`):
 * клиент не считает очки и не решает исход попытки, а только показывает снимок, держит
 * открытым ревил завершённого раунда до нажатия «Далее» и показывает уведомления.
 */
import type { MoveResult, RoundOut, SessionSnapshot } from "@/shared/api/types";

export type MoveOutcome = MoveResult["result"];

/** Уведомление поверх экрана: код определяет текст (ключ i18n `game.notice.<kind>`). */
export type NoticeKind = "rate_limited" | "round_void" | "network" | "desync" | "failed";

export type GameState = {
  snapshot: SessionSnapshot | null;
  /** Индекс раунда, чей ревил показан и ждёт «Далее»; `null` — играем текущий раунд. */
  revealRound: number | null;
  /** Исход последней попытки — для короткой обратной связи у поля ответа. */
  lastOutcome: MoveOutcome;
  /** Ход отправлен и ждёт ответа: кнопки блокируются, повторной отправки нет. */
  busy: boolean;
  notice: NoticeKind | null;
};

export type GameAction =
  | { type: "snapshot"; snapshot: SessionSnapshot }
  | { type: "move_started" }
  | { type: "move_succeeded"; result: MoveResult }
  | { type: "move_failed"; notice: NoticeKind | null }
  | { type: "continue" }
  | { type: "dismiss_notice" };

export const initialGameState: GameState = {
  snapshot: null,
  revealRound: null,
  lastOutcome: null,
  busy: false,
  notice: null,
};

const isClosed = (round: RoundOut | undefined): boolean =>
  round?.status === "finished" || round?.status === "void";

/**
 * Применить снимок. Устаревший (версия меньше текущей) игнорируется: ответы на ход и ресинк
 * могут прийти в любом порядке. Если раунд, который игрок видел активным, в новом снимке
 * завершён (ход дошёл до сервера, а ответ потерялся) — открываем его ревил.
 */
function applySnapshot(state: GameState, next: SessionSnapshot): GameState {
  const prev = state.snapshot;
  if (prev && prev.id === next.id && next.version < prev.version) return state;
  let revealRound = state.revealRound;
  if (prev && prev.id === next.id && revealRound === null) {
    const seen = prev.rounds.find((r) => r.index === prev.current_round);
    const now = next.rounds.find((r) => r.index === prev.current_round);
    if (seen && !isClosed(seen) && isClosed(now)) revealRound = prev.current_round;
  }
  return { ...state, snapshot: next, revealRound };
}

export function gameReducer(state: GameState, action: GameAction): GameState {
  switch (action.type) {
    case "snapshot":
      return applySnapshot(state, action.snapshot);
    case "move_started":
      return { ...state, busy: true, notice: null, lastOutcome: null };
    case "move_succeeded": {
      const { result } = action;
      const next = applySnapshot({ ...state, revealRound: null }, result.session);
      return {
        ...next,
        busy: false,
        lastOutcome: result.result,
        revealRound: isClosed(result.round) ? result.round.index : state.revealRound,
      };
    }
    case "move_failed":
      return { ...state, busy: false, notice: action.notice };
    case "continue":
      return { ...state, revealRound: null, lastOutcome: null };
    case "dismiss_notice":
      return { ...state, notice: null };
  }
}

/** Раунд на экране: открытый ревил или текущий раунд сессии. */
export function visibleRound(state: GameState): RoundOut | null {
  const s = state.snapshot;
  if (!s) return null;
  const index = state.revealRound ?? s.current_round;
  return s.rounds.find((r) => r.index === index) ?? null;
}

/** Сессия завершена и ревил последнего раунда закрыт — пора на экран итога. */
export function readyForResults(state: GameState): boolean {
  return state.snapshot?.status === "finished" && state.revealRound === null;
}
