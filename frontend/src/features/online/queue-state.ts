/**
 * Состояние очереди рейтинговой дуэли на клиенте (ADR 0038). Чистый редьюсер: ответы
 * `enter`/`tick` match превращаются в экран «ищем» / «соперник найден» / сообщение.
 */
import type { QueueFormat, QueueTicket } from "@/shared/api/types";

/** Ключи сообщений (i18n), которые ставит сам клиент, а не сервер. */
export const QUEUE_REMOVED_KEY = "rating.queue.removed";
export const QUEUE_SESSION_EXPIRED_KEY = "rating.queue.sessionExpired";

export type QueueState =
  | { phase: "idle" }
  | { phase: "entering"; ladder: string; format: QueueFormat }
  | {
      phase: "searching";
      ladder: string;
      format: QueueFormat;
      slow: boolean;
      waitS: number;
      /** Период heartbeat из ответа сервера, с; `null` — сервер не сообщил. */
      tickS: number | null;
      /** Идёт отмена (кнопка заблокирована). */
      leaving: boolean;
      /** Ключ сообщения над ожиданием (не удалось выйти); сбрасывается ответом tick. */
      notice: string | null;
    }
  | { phase: "matched"; ladder: string; format: QueueFormat; roomCode: string }
  | { phase: "error"; errorKey: string; roomCode: string | null };

export type QueueAction =
  | { type: "enter"; ladder: string; format: QueueFormat }
  | { type: "ticket"; ticket: QueueTicket }
  | { type: "leaving" }
  | { type: "leaveFailed"; errorKey: string }
  | { type: "left" }
  /** Heartbeat ответил 404: записи в очереди больше нет. */
  | { type: "gone" }
  | { type: "failed"; errorKey: string; roomCode: string | null };

export const initialQueue: QueueState = { phase: "idle" };

export function queueReducer(state: QueueState, action: QueueAction): QueueState {
  switch (action.type) {
    case "enter":
      return { phase: "entering", ladder: action.ladder, format: action.format };
    case "ticket": {
      // Найденный матч — финал: поздний tick состояние не откатывает.
      if (state.phase === "matched") return state;
      const { ticket } = action;
      if (ticket.state === "matched" && ticket.room_code) {
        return {
          phase: "matched",
          ladder: ticket.ladder,
          format: ticket.format,
          roomCode: ticket.room_code,
        };
      }
      return {
        phase: "searching",
        ladder: ticket.ladder,
        format: ticket.format,
        slow: ticket.slow,
        waitS: ticket.wait_s,
        tickS: ticket.tick_s ?? (state.phase === "searching" ? state.tickS : null),
        leaving: state.phase === "searching" ? state.leaving : false,
        notice: null,
      };
    }
    case "leaving":
      return state.phase === "searching" ? { ...state, leaving: true, notice: null } : state;
    case "leaveFailed":
      return state.phase === "searching"
        ? { ...state, leaving: false, notice: action.errorKey }
        : state;
    case "left":
      return initialQueue;
    case "gone":
      return state.phase === "idle"
        ? state
        : { phase: "error", errorKey: QUEUE_REMOVED_KEY, roomCode: null };
    case "failed":
      return { phase: "error", errorKey: action.errorKey, roomCode: action.roomCode };
  }
}
