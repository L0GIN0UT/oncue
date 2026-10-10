"use client";
/** Индикатор раундов: исход каждого раунда цветом, текущий — кольцом цвета домена. */
import { useTranslations } from "next-intl";
import type { RoundOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { roundOutcome, type RoundOutcome } from "./levels";

const TONE: Record<RoundOutcome, string> = {
  exact: "bg-exact",
  partial: "bg-partial",
  miss: "bg-miss",
  void: "bg-skip",
  active: "bg-transparent ring-2 ring-[var(--accent)]",
  pending: "bg-rule",
};

export function RoundRail({ rounds, current }: { rounds: RoundOut[]; current: number }) {
  const t = useTranslations();
  return (
    <ol
      className="flex items-center gap-1.5"
      aria-label={t("game.roundsLabel", { current: current + 1, total: rounds.length })}
    >
      {rounds.map((r) => {
        const outcome = roundOutcome(r);
        return (
          <li
            key={r.index}
            aria-current={r.index === current ? "step" : undefined}
            className={cn(
              "size-3 rounded-full ring-inset",
              TONE[outcome],
              r.index === current && "size-3.5",
            )}
          >
            <span className="sr-only">
              {t("game.roundItem", { index: r.index + 1 })}: {t(`game.outcome.${outcome}`)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
