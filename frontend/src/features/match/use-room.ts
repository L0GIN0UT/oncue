"use client";
/**
 * Комната на экране: {@link RoomLink} (канал + tick + ресинк) и редьюсер состояния.
 *
 * Команды идут через {@link useRoomCommands}: ответ команды лобби — полный снимок комнаты
 * (применяется, если не старее показанного), `guess` — своё состояние раунда, 202 — ждать
 * дельту, а не дождавшись — ресинк (D6).
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { GuessResult, RoomCommandResult } from "@/shared/api/types";
import {
  defaultTransportFactory,
  RoomLink,
  ServerClock,
  type LinkStatus,
  type TransportFactory,
} from "@/shared/realtime";
import { fetchRoomState, sendTick } from "./api";
import { sendCommand, type CommandBody, type CommandName, type CommandOk } from "./commands";
import { initialRoomStore, roomReducer, type RoomStore } from "./room-store";

export type UseRoom = {
  store: RoomStore;
  status: LinkStatus;
  fatal: unknown;
  clock: ServerClock;
  /** Отправить команду; ошибка — исключением (экран показывает её по коду). */
  command: <N extends CommandName>(name: N, body: CommandBody<N>) => Promise<CommandOk<N> | null>;
  /** Взять снимок сейчас (после 409 `match.phase_mismatch` и т. п.). */
  resync: () => void;
};

type Options = { enabled: boolean; transportFactory?: TransportFactory };

const isRoomResult = (data: unknown): data is RoomCommandResult =>
  !!data && typeof data === "object" && "room" in data && "version" in data;
const isGuessResult = (data: unknown): data is GuessResult =>
  !!data && typeof data === "object" && "me" in data && "attempts_left" in data;

export function useRoom(code: string, { enabled, transportFactory }: Options): UseRoom {
  const [store, dispatch] = useReducer(roomReducer, initialRoomStore);
  const [status, setStatus] = useState<LinkStatus>({ transport: null, offline: false });
  const [fatal, setFatal] = useState<unknown>(null);
  const linkRef = useRef<RoomLink | null>(null);
  const storeRef = useRef(store);
  // Часы живут дольше связи: пересоздание связи не сбрасывает поправку.
  const clock = useMemo(() => new ServerClock(), []);

  useEffect(() => {
    storeRef.current = store;
  }, [store]);

  useEffect(() => {
    if (!enabled) return;
    const factory = transportFactory ?? defaultTransportFactory();
    const link = new RoomLink({
      transport: factory(code),
      clock,
      fetchState: () => fetchRoomState(code),
      tick: (known) => sendTick(code, known),
      onState: (state) => dispatch({ type: "snapshot", state }),
      onMessage: (message) => dispatch({ type: "message", message }),
      onStatus: setStatus,
      onFatal: setFatal,
    });
    linkRef.current = link;
    link.start();
    return () => {
      link.stop();
      linkRef.current = null;
    };
  }, [code, enabled, transportFactory, clock]);

  // Личное состояние нового раунда приходит только снимком (ADR 0035).
  useEffect(() => {
    if (store.meStale) void linkRef.current?.resync();
  }, [store.meStale, store.state?.room.version]);

  const command = useCallback(
    async <N extends CommandName>(name: N, body: CommandBody<N>) => {
      const out = await sendCommand(code, name, body);
      const link = linkRef.current;
      if (out.kind === "accepted") {
        link?.awaitAccepted(out.version);
        return null;
      }
      const data: unknown = out.data;
      if (isRoomResult(data)) {
        const current = storeRef.current.state;
        link?.acceptSnapshot({
          room: data.room,
          match: current?.match ?? null,
          me: current?.me ?? null,
        });
      } else if (isGuessResult(data)) {
        dispatch({ type: "guess", result: data });
      }
      return out.data;
    },
    [code],
  );

  const resync = useCallback(() => void linkRef.current?.resync(), []);

  return { store, status, fatal, clock, command, resync };
}

/** Версия, которую видит клиент (`expected_version` команд фазы). */
export function visibleVersion(store: RoomStore): number {
  return store.state?.room.version ?? 0;
}
