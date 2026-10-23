"use client";
/**
 * Очередь рейтинговой дуэли: вход, heartbeat `tick` с периодом из ответа сервера (`tick_s`),
 * отмена. При загрузке хаба один `tick` восстанавливает поиск после перезагрузки страницы
 * (404 `match.not_in_queue` — записи нет). Пока ответа с периодом не было, heartbeat не идёт.
 *
 * Правила:
 * - вернулись на вкладку — `tick` сразу (фоновая вкладка тикает редко, запись могла истечь);
 * - 404 на `tick` в поиске — «вас убрали из очереди»; 401/403 — «сессия истекла», остановка;
 * - отмена: 404 `not_in_queue` = успех; провал оставляет поиск (запись жива) с сообщением;
 * - уход со страницы в `entering`/`searching` снимает игрока с очереди, а ответ `enter`,
 *   пришедший после ухода, не применяется — сразу `leave`.
 */
import { useCallback, useEffect, useReducer, useRef } from "react";
import { isApiError } from "@/shared/api/errors";
import type { QueueFormat } from "@/shared/api/types";
import { enterQueue, leaveQueue, tickQueue } from "@/features/match/queue-api";
import { matchErrorKey } from "@/features/match/errors";
import { initialQueue, QUEUE_SESSION_EXPIRED_KEY, queueReducer } from "./queue-state";

/** Пауза между повторами `leave` при 409 `match.queue_matching` и число повторов (пара резервируется). */
const LEAVE_RETRY_MS = 1000;
const LEAVE_RETRIES = 2;

export function useQueue() {
  const [state, dispatch] = useReducer(queueReducer, initialQueue);
  const phase = useRef(state.phase);
  const inFlight = useRef(false);
  // Поколение очереди: ответ, начатый до входа или отмены, устарел и отбрасывается.
  const epoch = useRef(0);
  // Идёт отмена: новые tick не стартуют, иначе запрос до `leave` вернёт игрока в поиск.
  const leaving = useRef(false);
  const mounted = useRef(false);
  const retry = useRef<{ timer: ReturnType<typeof setTimeout>; cancel: () => void } | null>(null);

  useEffect(() => {
    phase.current = state.phase;
  }, [state.phase]);

  const tick = useCallback(async () => {
    if (inFlight.current || leaving.current) return;
    inFlight.current = true;
    const started = epoch.current;
    try {
      const ticket = await tickQueue();
      if (epoch.current === started) dispatch({ type: "ticket", ticket });
    } catch (e) {
      if (epoch.current !== started) return;
      // Сообщения — только если игрок действительно ждал: при загрузке хаба 404 — норма.
      const waiting = phase.current === "searching";
      if (isApiError(e, "match.not_in_queue")) {
        if (waiting) dispatch({ type: "gone" });
      } else if (isApiError(e) && (e.status === 401 || e.status === 403)) {
        if (waiting) {
          dispatch({ type: "failed", errorKey: QUEUE_SESSION_EXPIRED_KEY, roomCode: null });
        }
      }
      // Сеть, 429 и прочее — повторим на следующем тике.
    } finally {
      inFlight.current = false;
    }
  }, []);

  // Монтирование: восстановление поиска после перезагрузки; размонтирование: снять с очереди.
  useEffect(() => {
    mounted.current = true;
    void tick();
    return () => {
      mounted.current = false;
      retry.current?.cancel();
      if (phase.current === "searching" || phase.current === "entering") {
        void leaveQueue().catch(() => undefined);
      }
    };
  }, [tick]);

  const searching = state.phase === "searching";
  const tickS = state.phase === "searching" ? state.tickS : null;

  useEffect(() => {
    if (!searching || !tickS) return;
    const timer = setInterval(() => void tick(), tickS * 1000);
    return () => clearInterval(timer);
  }, [searching, tickS, tick]);

  // Вкладка снова видима — не ждём интервала (в фоне таймеры замедлены).
  useEffect(() => {
    if (!searching) return;
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [searching, tick]);

  const enter = useCallback(async (ladder: string, format: QueueFormat) => {
    const mine = ++epoch.current;
    dispatch({ type: "enter", ladder, format });
    try {
      const ticket = await enterQueue(ladder, format);
      if (!mounted.current || epoch.current !== mine) {
        // Ушли со страницы или действие устарело, а запись уже создана — снимаем её.
        void leaveQueue().catch(() => undefined);
        return;
      }
      dispatch({ type: "ticket", ticket });
    } catch (e) {
      if (!mounted.current || epoch.current !== mine) return;
      const code = isApiError(e, "match.active_room_conflict") ? e.details?.code : null;
      dispatch({
        type: "failed",
        errorKey: matchErrorKey(e),
        roomCode: typeof code === "string" ? code : null,
      });
    }
  }, []);

  /** Пауза, прерываемая размонтированием: `false` — отменена. */
  const pause = useCallback(
    (ms: number) =>
      new Promise<boolean>((resolve) => {
        const timer = setTimeout(() => {
          retry.current = null;
          resolve(true);
        }, ms);
        retry.current = {
          timer,
          cancel: () => {
            clearTimeout(timer);
            retry.current = null;
            resolve(false);
          },
        };
      }),
    [],
  );

  const leave = useCallback(async () => {
    if (leaving.current) return;
    leaving.current = true;
    epoch.current += 1;
    dispatch({ type: "leaving" });
    try {
      for (let attempt = 0; ; attempt++) {
        try {
          await leaveQueue();
          break;
        } catch (e) {
          // Записи уже нет — цель достигнута.
          if (isApiError(e, "match.not_in_queue")) break;
          // Пара как раз резервируется — повторяем с паузой.
          if (!isApiError(e, "match.queue_matching") || attempt >= LEAVE_RETRIES) throw e;
          if (!(await pause(LEAVE_RETRY_MS))) return;
        }
      }
      epoch.current += 1;
      if (mounted.current) dispatch({ type: "left" });
    } catch (e) {
      // Не вышли: запись жива, остаёмся в поиске (heartbeat продолжается) и говорим об этом.
      if (mounted.current) {
        dispatch({
          type: "leaveFailed",
          errorKey: isApiError(e, "match.queue_matching")
            ? "rating.queue.leaveFailed"
            : matchErrorKey(e),
        });
      }
    } finally {
      leaving.current = false;
    }
  }, [pause]);

  const dismiss = useCallback(() => dispatch({ type: "left" }), []);

  return { state, enter, leave, dismiss };
}
