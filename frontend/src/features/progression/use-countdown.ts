"use client";
/** Обратный отсчёт в секундах (для `Retry-After`): `start(n)` запускает, по нулю останавливается. */
import { useCallback, useEffect, useState } from "react";

export function useCountdown(): { remaining: number; start: (seconds: number) => void } {
  const [remaining, setRemaining] = useState(0);
  const active = remaining > 0;

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setRemaining((r) => Math.max(0, r - 1)), 1000);
    return () => clearInterval(timer);
  }, [active]);

  const start = useCallback((seconds: number) => setRemaining(Math.max(0, Math.ceil(seconds))), []);
  return { remaining, start };
}
