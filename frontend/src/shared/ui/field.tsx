/** Поле формы с подписью и сообщением об ошибке (связано через aria-describedby). */
import { useId, type ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

type Props = ComponentProps<"input"> & { label: string; error?: string | null; hint?: string };

export function Field({ label, error, hint, className, ...props }: Props) {
  const id = useId();
  const describedBy = [error ? `${id}-error` : null, hint ? `${id}-hint` : null]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        className={cn(
          "h-12 rounded-[var(--radius-control)] bg-night px-4 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-music aria-[invalid=true]:ring-miss",
          className,
        )}
        {...props}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-xs text-ink-faint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-sm text-miss">
          {error}
        </p>
      )}
    </div>
  );
}
