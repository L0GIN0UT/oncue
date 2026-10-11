/**
 * Ходы игры (ответ, пропуск, «сдаться») — напрямую в game (D4).
 *
 * У каждого хода свой `Idempotency-Key`. При сетевой ошибке ход повторяется с тем же ключом:
 * если первый запрос дошёл до сервера, повтор вернёт сохранённый ответ, а не второй ход.
 */
import { api, unwrap } from "@/shared/api/client";
import { NetworkError } from "@/shared/api/errors";
import type { Answer, MoveResult } from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

export type Move = { kind: "guess"; answer: Answer } | { kind: "skip" } | { kind: "give_up" };

export type MoveTarget = { sessionId: string; roundIndex: number; expectedVersion: number };

export type SendOptions = {
  /** Ключ идемпотентности; по умолчанию — новый на каждый вызов `sendMove`. */
  key?: string;
  /** Повторов после сетевой ошибки. */
  retries?: number;
  /** Пауза перед повтором (мс), растёт линейно. */
  backoffMs?: number;
  sleep?: (ms: number) => Promise<void>;
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function post(target: MoveTarget, move: Move, key: string) {
  const { game } = api();
  const header = { "Idempotency-Key": key };
  const session_id = target.sessionId;
  const expected_version = target.expectedVersion;
  switch (move.kind) {
    case "guess":
      return game.POST("/v1/sessions/{session_id}/guesses", {
        params: { path: { session_id }, header },
        body: { round_index: target.roundIndex, expected_version, answer: move.answer },
      });
    case "skip":
      return game.POST("/v1/sessions/{session_id}/skips", {
        params: { path: { session_id }, header },
        body: { round_index: target.roundIndex, expected_version },
      });
    case "give_up":
      return game.POST("/v1/sessions/{session_id}/rounds/{index}/give-up", {
        params: { path: { session_id, index: target.roundIndex }, header },
        body: { expected_version },
      });
  }
}

/** Отправить ход; бросает `ApiError` (ответ сервера) или `NetworkError` (повторы исчерпаны). */
export async function sendMove(
  target: MoveTarget,
  move: Move,
  options: SendOptions = {},
): Promise<MoveResult> {
  const key = options.key ?? newIdempotencyKey();
  const retries = options.retries ?? 2;
  const backoff = options.backoffMs ?? 400;
  const sleep = options.sleep ?? defaultSleep;
  for (let attempt = 0; ; attempt++) {
    try {
      return unwrap<MoveResult>(await post(target, move, key));
    } catch (e) {
      if (!(e instanceof NetworkError) || attempt >= retries) throw e;
      await sleep(backoff * (attempt + 1));
    }
  }
}
