"use client";
/**
 * Поле пароля: подпись (справа можно вынести ссылку, например «Забыл пароль») и кнопка показа
 * символов в правом краю поля. Кнопка — настоящая `button` с `aria-pressed`, подпись без слова
 * «пароль», чтобы не путаться с подписью самого поля.
 */
import { useId, useState, type ComponentProps, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { cn } from "@/shared/lib/cn";
import { inputClass } from "@/shared/ui/field";

type Props = Omit<ComponentProps<"input">, "type"> & {
  label: string;
  /** Справа от подписи: ссылка «Забыл пароль». */
  aside?: ReactNode;
  hint?: string;
};

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden
      className="size-[22px] fill-none stroke-current stroke-2 [stroke-linecap:round] [stroke-linejoin:round]"
    >
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
      {off && <path d="M4 4l16 16" />}
    </svg>
  );
}

export function PasswordField({ label, aside, hint, className, ...props }: Props) {
  const t = useTranslations();
  const id = useId();
  const [shown, setShown] = useState(false);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={id} className="text-sm font-medium text-ink-muted">
          {label}
        </label>
        {aside}
      </div>
      <div className="relative flex items-center">
        <input
          id={id}
          type={shown ? "text" : "password"}
          aria-describedby={hint ? `${id}-hint` : undefined}
          className={cn(inputClass, "pr-14", className)}
          {...props}
        />
        <button
          type="button"
          aria-pressed={shown}
          aria-label={shown ? t("auth.hideChars") : t("auth.showChars")}
          onClick={() => setShown((v) => !v)}
          className="absolute right-1 grid size-11 place-items-center rounded-full text-ink-muted hover:text-ink"
        >
          <EyeIcon off={shown} />
        </button>
      </div>
      {hint && (
        <p id={`${id}-hint`} className="text-[13px] text-ink-faint">
          {hint}
        </p>
      )}
    </div>
  );
}
