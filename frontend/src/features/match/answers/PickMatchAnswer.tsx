"use client";
/**
 * Формат `pick` в матче: варианты раунда — только в своём состоянии (`me.options`, D9);
 * `option_id` непрозрачны и перемешаны на комнату. Клавиши — как в соло (`1…9`, `←`/`→`).
 */
import { useEffect, useMemo } from "react";
import { useTranslations } from "next-intl";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { optionForKey } from "@/features/game/answers/pick";
import type { MatchAnswerProps } from "./types";

const fromField = (target: EventTarget | null) =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

export function PickMatchAnswer({ me, disabled, onGuess }: MatchAnswerProps) {
  const t = useTranslations();
  const dt = useDynamicT();
  const options = useMemo(() => me?.options ?? [], [me?.options]);

  useEffect(() => {
    if (disabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || fromField(e.target)) return;
      const id = optionForKey(e.key, options);
      if (!id) return;
      e.preventDefault();
      onGuess({ option_id: id });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [disabled, onGuess, options]);

  return (
    <ul className="grid gap-2.5" aria-label={t("game.optionsLabel")}>
      {options.map((option, i) => (
        <li key={option.option_id}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onGuess({ option_id: option.option_id })}
            data-testid={`match-option-${i}`}
            aria-keyshortcuts={String(i + 1)}
            className="flex min-h-[68px] w-full items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-[18px] py-2 text-left transition-colors hover:border-rule-strong disabled:pointer-events-none disabled:opacity-45"
          >
            <kbd
              aria-hidden
              className="tabular rounded-md border-[1.5px] border-rule px-2 py-0.5 text-sm text-ink-muted"
            >
              {i + 1}
            </kbd>
            <span className="font-display text-base font-medium" dir="auto">
              {option.title ??
                (option.label_key ? dt(option.label_key, option.option_id) : option.option_id)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
