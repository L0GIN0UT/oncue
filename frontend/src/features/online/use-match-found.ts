"use client";
/**
 * Соперник найден: короткий сигнал, а если вкладка в фоне — мигающий заголовок, пока игрок не
 * вернётся; затем переход в комнату (`go`). Видимая вкладка переходит после короткой паузы,
 * чтобы успеть прочитать «Соперник найден».
 */
import { useEffect, useRef } from "react";
import { flashTitle, playFoundSignal } from "./title-flash";

/** Пауза перед переходом на видимой вкладке, мс. */
export const FOUND_DELAY_MS = 900;

export function useMatchFound(
  roomCode: string | null,
  flashText: string,
  go: (code: string) => void,
) {
  const goRef = useRef(go);
  useEffect(() => {
    goRef.current = go;
  });
  useEffect(() => {
    if (!roomCode) return;
    playFoundSignal();
    if (document.hidden) {
      const stopFlash = flashTitle(flashText);
      const onVisible = () => {
        if (document.hidden) return;
        stopFlash();
        document.removeEventListener("visibilitychange", onVisible);
        goRef.current(roomCode);
      };
      document.addEventListener("visibilitychange", onVisible);
      return () => {
        stopFlash();
        document.removeEventListener("visibilitychange", onVisible);
      };
    }
    const timer = setTimeout(() => goRef.current(roomCode), FOUND_DELAY_MS);
    return () => clearTimeout(timer);
  }, [roomCode, flashText]);
}
