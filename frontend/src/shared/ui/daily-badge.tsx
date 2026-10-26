/**
 * Бейдж результата дейлика в ленте друзей: «♪ 5» (музыка) и «▣ 4» (кино) с цветом исхода.
 * Нет результата — «—» серым. Исход считается от доли очков (`dailyOutcome`).
 */
import { cn } from "@/shared/lib/cn";

export type DailyOutcome = "exact" | "partial" | "miss" | "skip";

const GLYPH = { music: "♪", film: "▣" } as const;

const STYLE: Record<DailyOutcome, string> = {
  exact: "bg-exact-fill text-exact",
  partial: "bg-partial-fill text-partial",
  miss: "bg-miss-fill text-miss",
  skip: "bg-skip-fill text-skip",
};

/** Доля ≥ 0,8 — точно, ≥ 0,4 — почти, ниже — мимо; нет результата — пропуск. */
export function dailyOutcome(value: number | null, max: number): DailyOutcome {
  if (value == null || max <= 0) return "skip";
  const share = value / max;
  if (share >= 0.8) return "exact";
  if (share >= 0.4) return "partial";
  return "miss";
}

export function DailyBadge({
  domain,
  value,
  max = 5,
  className,
}: {
  domain: "music" | "film";
  value: number | null;
  /** Максимум, от которого считается цвет исхода. */
  max?: number;
  className?: string;
}) {
  const outcome = dailyOutcome(value, max);
  return (
    <span
      data-outcome={outcome}
      className={cn(
        "inline-flex h-[18px] min-w-6 items-center justify-center gap-[3px] rounded-full px-[5px] text-[10px] font-bold",
        STYLE[outcome],
        className,
      )}
    >
      <span aria-hidden>{GLYPH[domain]}</span>
      {value ?? "—"}
    </span>
  );
}
