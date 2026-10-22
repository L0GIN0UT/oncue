/**
 * Команды комнаты и матча — напрямую в match (`POST /v1/rooms/{code}/...`).
 *
 * У каждой команды свой `op_id` (uuid): повтор после сетевой ошибки идёт с тем же `op_id`, и
 * владелец комнаты применит команду один раз. Команды фазы несут `expected_version` — версию,
 * которую видит клиент. 202 `CommandAccepted` — владелец не успел ответить: результат придёт
 * дельтой канала или ресинком (D6).
 */
import { API_PREFIX } from "@/shared/api/client";
import { NetworkError, toApiError } from "@/shared/api/errors";
import { apiFetch } from "@/shared/api/http";
import type { paths as MatchPaths } from "@/shared/api/schema/match";
import type { CommandAccepted } from "@/shared/api/types";
import { newIdempotencyKey } from "@/shared/lib/id";

type RoomPath = Extract<keyof MatchPaths, `/v1/rooms/{code}/${string}`>;
type PostOf<P extends RoomPath> = MatchPaths[P]["post"];
type JsonBody<O> = O extends { requestBody: { content: { "application/json": infer B } } }
  ? B
  : never;
type JsonOk<O> = O extends { responses: { 200: { content: { "application/json": infer R } } } }
  ? R
  : never;

/** Пути команд с телом `op_id` (всё, кроме tick и realtime-token). */
export type CommandPath = {
  [P in RoomPath]: [JsonBody<PostOf<P>>] extends [never]
    ? never
    : JsonBody<PostOf<P>> extends { op_id: string }
      ? P
      : never;
}[RoomPath];

/** Короткое имя команды: `ready`, `match/guess`, … */
export type CommandName = CommandPath extends `/v1/rooms/{code}/${infer N}` ? N : never;
type PathOf<N extends CommandName> = `/v1/rooms/{code}/${N}` & CommandPath;

export type CommandBody<N extends CommandName> = Omit<JsonBody<PostOf<PathOf<N>>>, "op_id">;
export type CommandOk<N extends CommandName> = JsonOk<PostOf<PathOf<N>>>;

export type CommandOutcome<N extends CommandName> =
  { kind: "done"; data: CommandOk<N> } | { kind: "accepted"; version: number };

export type CommandOptions = {
  opId?: string;
  retries?: number;
  backoffMs?: number;
  sleep?: (ms: number) => Promise<void>;
  fetch?: (request: Request) => Promise<Response>;
};

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function commandUrl(code: string, name: CommandName): string {
  return `${globalThis.location.origin}${API_PREFIX.match}/v1/rooms/${encodeURIComponent(code)}/${name}`;
}

/** Отправить команду; бросает `ApiError` (ответ сервера) или `NetworkError` (повторы исчерпаны). */
export async function sendCommand<N extends CommandName>(
  code: string,
  name: N,
  body: CommandBody<N>,
  options: CommandOptions = {},
): Promise<CommandOutcome<N>> {
  const opId = options.opId ?? newIdempotencyKey();
  const retries = options.retries ?? 2;
  const backoff = options.backoffMs ?? 400;
  const sleep = options.sleep ?? defaultSleep;
  const doFetch = options.fetch ?? apiFetch;
  const payload = JSON.stringify({ ...body, op_id: opId });
  for (let attempt = 0; ; attempt++) {
    let res: Response;
    try {
      res = await doFetch(
        new Request(commandUrl(code, name), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
        }),
      );
    } catch (e) {
      if (!(e instanceof NetworkError) || attempt >= retries) throw e;
      await sleep(backoff * (attempt + 1));
      continue;
    }
    if (res.status === 202) {
      const accepted = (await res.json()) as CommandAccepted;
      return { kind: "accepted", version: accepted.version };
    }
    if (!res.ok) throw await toApiError(res);
    return { kind: "done", data: (await res.json()) as CommandOk<N> };
  }
}
