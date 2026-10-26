/** Знак «Guessverse»: слово Unbounded 800 с плотным трекингом, без значка. Размер задаёт родитель. */
import { cn } from "@/shared/lib/cn";

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display font-extrabold tracking-[-0.03em] text-ink", className)}>
      Guessverse
    </span>
  );
}
