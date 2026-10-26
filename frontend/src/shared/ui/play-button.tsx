"use client";
/** Большая круглая кнопка Play (88 px); во время воспроизведения пульсирует (только по действию). */
import type { ComponentProps } from "react";
import { Pause, Play } from "lucide-react";
import { cn } from "@/shared/lib/cn";

type Props = Omit<ComponentProps<"button">, "children"> & {
  playing?: boolean;
  /** Имя действия: «Слушать» / «Пауза». */
  "aria-label": string;
};

export function PlayButton({ playing = false, className, ...props }: Props) {
  const Icon = playing ? Pause : Play;
  return (
    <button
      type="button"
      aria-pressed={playing}
      className={cn(
        "grid size-[88px] shrink-0 place-items-center rounded-full bg-music text-night shadow-[0_0_0_6px_var(--color-glow)] transition-transform hover:scale-[1.04] disabled:pointer-events-none disabled:opacity-45",
        playing && "animate-pulse-ring",
        className,
      )}
      {...props}
    >
      <Icon className={cn("size-8 fill-current", !playing && "ml-[3px]")} aria-hidden />
    </button>
  );
}
