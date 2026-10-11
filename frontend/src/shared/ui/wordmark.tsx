/** Словесный знак OnCue: «реплика» — короткий отрезок перед словом, как метка в партитуре. */
import { cn } from "@/shared/lib/cn";

export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 font-display font-bold tracking-tight",
        className,
      )}
    >
      <span aria-hidden className="flex h-[0.9em] items-end gap-[2px]">
        <span className="h-[35%] w-[3px] rounded-full bg-music" />
        <span className="h-[65%] w-[3px] rounded-full bg-music" />
        <span className="h-full w-[3px] rounded-full bg-film" />
      </span>
      OnCue
    </span>
  );
}
