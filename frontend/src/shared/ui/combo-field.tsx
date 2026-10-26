"use client";
/**
 * Поле ответа с подсказками, которые раскрываются вверх: combobox (ARIA 1.2) со стрелками,
 * Enter выбирает подсвеченную подсказку (по умолчанию первую), крестик очищает поле.
 * Содержимое подсказки (`label`: исполнитель жирным, год справа) собирает вызывающий.
 */
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/shared/lib/cn";
import { inputClass } from "./field";

export type ComboOption = { id: string; label: ReactNode };

type Props = {
  /** Подпись поля для скринридера (визуально скрыта: подсказка — placeholder). */
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly ComboOption[];
  onSelect: (option: ComboOption) => void;
  clearLabel: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
};

export function ComboField({
  label,
  value,
  onChange,
  options,
  onSelect,
  clearLabel,
  placeholder,
  disabled,
  className,
  "data-testid": testId,
}: Props) {
  const uid = useId();
  const listId = `${uid}-list`;
  const [active, setActive] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  const open = !dismissed && options.length > 0 && value.length > 0;
  const current = Math.min(active, Math.max(options.length - 1, 0));

  function pick(o: ComboOption) {
    onSelect(o);
    setDismissed(true);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (options.length === 0) return;
      e.preventDefault();
      setDismissed(false);
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + options.length) % options.length);
    } else if (e.key === "Enter" && open) {
      e.preventDefault();
      pick(options[current]!);
    } else if (e.key === "Escape" && open) {
      e.stopPropagation();
      setDismissed(true);
    }
  }

  return (
    <div className={cn("relative", className)}>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label={label}
          className="absolute right-0 bottom-[calc(100%+4px)] left-0 z-10 max-h-72 animate-rise overflow-y-auto rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage"
        >
          {options.map((o, i) => (
            <li
              key={o.id}
              id={`${uid}-opt-${i}`}
              role="option"
              aria-selected={i === current}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(o)}
              onMouseEnter={() => setActive(i)}
              className={cn(
                "flex cursor-pointer items-center justify-between gap-3 px-4 py-3 text-[15px]",
                i === current ? "bg-stage-raised" : "text-ink-muted",
              )}
            >
              {o.label}
            </li>
          ))}
        </ul>
      )}
      <input
        type="text"
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open ? `${uid}-opt-${current}` : undefined}
        aria-autocomplete="list"
        autoComplete="off"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        data-testid={testId}
        onChange={(e) => {
          setDismissed(false);
          setActive(0);
          onChange(e.target.value);
        }}
        onKeyDown={onKeyDown}
        className={cn(inputClass, value && "pr-12")}
      />
      {value && !disabled && (
        <button
          type="button"
          aria-label={clearLabel}
          onClick={() => {
            onChange("");
            setDismissed(false);
          }}
          className="absolute top-1/2 right-1 grid size-11 -translate-y-1/2 place-items-center rounded-full text-ink-muted hover:text-ink"
        >
          <X className="size-[18px]" aria-hidden />
        </button>
      )}
    </div>
  );
}
