/** Поле формы с подписью и сообщением об ошибке (связано через aria-describedby). */
import { useId, type ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

type Props = ComponentProps<"input"> & { label: string; error?: string | null; hint?: string };

/** Класс самого поля ввода: общий для `Field` и поля ответа с подсказками. */
export const inputClass =
  "h-[52px] w-full rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-4 text-base text-ink outline-none placeholder:text-ink-faint focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)] aria-[invalid=true]:border-miss";

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
        className={cn(inputClass, className)}
        {...props}
      />
      {hint && (
        <p id={`${id}-hint`} className="text-[13px] text-ink-faint">
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
