"use client";
/**
 * Таймер фазы по `phase_ends_at` сервера с поправкой часов (`server_now`, ADR 0035): клиент
 * ничего не решает по своему времени — фазу закрывает сервер, таймер только показывает остаток.
 */
import { useTranslations } from "next-intl";
import type { ServerClock } from "@/shared/realtime";
import { useNow } from "@/features/daily/use-now";
import { cn } from "@/shared/lib/cn";

type Props = {
  clock: ServerClock;
  endsAt: string | null | undefined;
  /** Полная длина фазы, с — для полосы (окно уровня); без неё — только секунды. */
  totalS?: number | null;
  className?: string;
};

export function useRemainingSeconds(clock: ServerClock, endsAt: string | null | undefined) {
  const now = useNow();
  if (!now) return null;
  const ms = clock.remainingMs(endsAt);
  return ms === null ? null : Math.ceil(ms / 1000);
}

export function PhaseTimer({ clock, endsAt, totalS, className }: Props) {
  const t = useTranslations();
  const seconds = useRemainingSeconds(clock, endsAt);
  if (seconds === null) return null;
  const fraction = totalS ? Math.min(1, seconds / totalS) : null;
  return (
    <div className={cn("flex items-center gap-3", className)} data-testid="phase-timer">
      {fraction !== null && (
        <div
          className="h-1.5 flex-1 overflow-hidden rounded-full bg-stage-raised"
          role="progressbar"
          aria-label={t("match.timer")}
          aria-valuemin={0}
          aria-valuemax={totalS ?? 0}
          aria-valuenow={seconds}
        >
          <div
            className={cn(
              "h-full rounded-full bg-[var(--accent)] transition-[width] duration-1000 ease-linear",
              seconds <= 5 && "bg-miss",
            )}
            style={{ width: `${fraction * 100}%` }}
          />
        </div>
      )}
      <span
        className={cn("tabular font-display text-lg font-semibold", seconds <= 5 && "text-miss")}
        aria-live={seconds <= 5 ? "polite" : "off"}
      >
        {t("match.seconds", { seconds })}
      </span>
    </div>
  );
}
