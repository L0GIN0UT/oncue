/**
 * Знак «Guessverse»: логотип и слово Unbounded 800 с плотным трекингом. «Guess» — цвет основного
 * текста, «verse» — акцент темы. Размер задаёт родитель (`text-*`): высота знака 1.5em.
 * Единственное место, где рисуется знак: шапка сайта и оболочка игры.
 */
import { cn } from "@/shared/lib/cn";

export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-[0.4em] font-display font-extrabold tracking-[-0.03em] whitespace-nowrap",
        className,
      )}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- статичный SVG-знак, оптимизация не нужна */}
      <img
        src="/logo.svg"
        alt=""
        aria-hidden
        width={372}
        height={350}
        data-testid="wordmark-logo"
        className="h-[1.5em] w-auto flex-none"
      />
      <span>
        <span data-testid="wordmark-guess" className="text-ink">
          Guess
        </span>
        <span data-testid="wordmark-verse" className="text-music">
          verse
        </span>
      </span>
    </span>
  );
}
