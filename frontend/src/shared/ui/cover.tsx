/**
 * Обложка, постер или кадр: картинка из бэкенда или плоская заглушка токенного цвета. Чип в углу
 * (`label`) — подпись поверх нижнего градиента. Размер задаёт родитель (ширина), форму — `ratio`.
 */
import type { ReactNode } from "react";
import { cn } from "@/shared/lib/cn";

const RATIOS = {
  square: "aspect-square",
  poster: "aspect-[2/3]",
  frame: "aspect-video",
} as const;

export type CoverRatio = keyof typeof RATIOS;

export function Cover({
  src,
  alt = "",
  ratio = "square",
  label,
  placeholder,
  className,
}: {
  src?: string | null;
  alt?: string;
  ratio?: CoverRatio;
  /** Чип в нижнем углу. */
  label?: ReactNode;
  /** Содержимое заглушки (например, иконка домена), когда картинки нет. */
  placeholder?: ReactNode;
  className?: string;
}) {
  return (
    <div
      data-testid="cover"
      className={cn(
        "relative overflow-hidden rounded-[10px] bg-stage-raised",
        RATIOS[ratio],
        className,
      )}
    >
      {src ? (
        // Картинка приходит с бэкенда (media), размеры заранее неизвестны: оптимизатор Next не нужен.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt={alt} className="size-full object-cover" />
      ) : (
        <span aria-hidden className="grid size-full place-items-center text-ink-dim">
          {placeholder}
        </span>
      )}
      {label != null && (
        <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-night/85 to-transparent px-2 pt-3.5 pb-1.5 text-[11px] font-semibold">
          {label}
        </span>
      )}
    </div>
  );
}
