"use client";
/**
 * Поле ответа: ARIA combobox с подсказками из catalog. Выбранная подсказка подставляется в поле
 * и уходит единицей (`media_item_id`); без выбора уходит свободный текст (сервер сверяет его
 * matcher-ом). Правка текста после выбора сбрасывает выбор.
 */
import { useId, useState, type KeyboardEvent } from "react";
import { useTranslations } from "next-intl";
import type { Answer, Domain, Suggestion } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useAutocomplete } from "./use-autocomplete";

type Props = {
  domain: Domain;
  disabled: boolean;
  onAnswer: (answer: Answer) => void;
  inputRef?: React.Ref<HTMLInputElement>;
};

const TEXT_MAX = 120;

export function AnswerBox({ domain, disabled, onAnswer, inputRef }: Props) {
  const t = useTranslations();
  const id = useId();
  const listId = `${id}-list`;
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Suggestion | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const { items, loading } = useAutocomplete(selected ? "" : text, domain);
  const listOpen = open && !selected && items.length > 0;

  const reset = () => {
    setText("");
    setSelected(null);
    setActive(-1);
    setOpen(false);
  };

  const choose = (s: Suggestion) => {
    setSelected(s);
    setText(s.title);
    setOpen(false);
    setActive(-1);
  };

  const submit = () => {
    if (disabled) return;
    if (selected) onAnswer({ media_item_id: selected.id });
    else {
      const value = text.trim().slice(0, TEXT_MAX);
      if (!value) return;
      onAnswer({ text: value });
    }
    reset();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const highlighted = listOpen && active >= 0 ? items[active] : undefined;
      if (highlighted) choose(highlighted);
      else submit();
    } else if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  };

  return (
    <form
      className="relative flex flex-col gap-3 sm:flex-row"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="relative flex-1">
        <label htmlFor={`${id}-input`} className="sr-only">
          {t(`game.answerLabel.${domain}`)}
        </label>
        <input
          ref={inputRef}
          id={`${id}-input`}
          type="text"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={listOpen}
          aria-controls={listId}
          aria-activedescendant={listOpen && active >= 0 ? `${id}-opt-${active}` : undefined}
          aria-busy={loading}
          autoComplete="off"
          spellCheck={false}
          maxLength={TEXT_MAX}
          disabled={disabled}
          placeholder={t(`game.answerPlaceholder.${domain}`)}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setSelected(null);
            setOpen(true);
            setActive(-1);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={onKeyDown}
          className="h-14 w-full rounded-[var(--radius-control)] bg-night px-4 text-lg text-ink ring-1 ring-rule outline-none ring-inset placeholder:text-ink-faint focus:ring-2 focus:ring-[var(--accent)] disabled:opacity-50"
        />
        <ul
          id={listId}
          role="listbox"
          aria-label={t("game.suggestions")}
          hidden={!listOpen}
          className="absolute inset-x-0 bottom-full z-20 mb-2 max-h-72 overflow-auto rounded-[var(--radius-control)] bg-stage-raised p-1 shadow-2xl ring-1 ring-rule"
        >
          {items.map((s, i) => (
            <li
              key={s.id}
              id={`${id}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(s)}
              className={cn(
                "flex cursor-pointer items-baseline justify-between gap-3 rounded-lg px-3 py-2.5",
                i === active ? "bg-[var(--accent)] text-night" : "hover:bg-rule/60",
              )}
            >
              <span className="font-medium">{s.title}</span>
              <span
                className={cn(
                  "shrink-0 text-sm",
                  i === active ? "text-night/75" : "text-ink-muted",
                )}
              >
                {[s.subtitle, s.year].filter(Boolean).join(", ")}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <Button type="submit" size="lg" disabled={disabled || !text.trim()}>
        {t("game.submit")}
      </Button>
    </form>
  );
}
