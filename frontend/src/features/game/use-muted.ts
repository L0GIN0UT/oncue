"use client";
/** Звук игры выключен: выбор игрока живёт в браузере (localStorage) и общий для всех экранов игры. */
import { useSyncExternalStore } from "react";

const KEY = "gv.game.muted";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function setMuted(value: boolean): void {
  try {
    localStorage.setItem(KEY, value ? "1" : "0");
  } catch {
    /* хранилище недоступно (приватный режим): выбор действует до перезагрузки */
  }
  listeners.forEach((l) => l());
}

export function useMuted(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}
