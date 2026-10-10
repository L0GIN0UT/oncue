"use client";
/** Табло отсчёта до смены дня. До гидрации — прочерки (время сервера и клиента различаются). */
import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { formatClock, remainingUntil } from "./countdown";
import { useNow } from "./use-now";

export function Countdown({ target, onElapsed }: { target: string; onElapsed?: () => void }) {
  const t = useTranslations();
  const now = useNow();
  const remaining = now ? remainingUntil(new Date(target), now) : null;
  const elapsed = remaining !== null && remaining.totalMs === 0;
  useEffect(() => {
    if (elapsed) onElapsed?.();
  }, [elapsed, onElapsed]);
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 text-ink-muted">
      <span>{t("daily.nextIn")}</span>
      <time
        dateTime={target}
        className="tabular font-display text-2xl font-semibold text-ink sm:text-3xl"
        data-testid="countdown"
        aria-label={
          remaining ? t("daily.countdownLabel", { clock: formatClock(remaining) }) : undefined
        }
      >
        {remaining ? formatClock(remaining) : "––:––:––"}
      </time>
    </p>
  );
}
