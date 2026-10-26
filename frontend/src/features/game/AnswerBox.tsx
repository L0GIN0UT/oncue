"use client";
/**
 * Поле ответа: подсказки catalog раскрываются вверх (исполнитель жирным, год справа), Enter
 * выбирает первую подсказку, крестик очищает. Выбранная подсказка уходит единицей (`media_item_id`);
 * без выбора уходит свободный текст (сервер сверяет его matcher-ом). Правка текста после выбора
 * сбрасывает выбор. Рядом с полем одна кнопка: «Пропустить», пока ответа нет, и «Ответить», когда
 * есть вариант или текст.
 */
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import type { Answer, Domain, Suggestion } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { ComboField, type ComboOption } from "@/shared/ui/combo-field";
import { useAutocomplete } from "./use-autocomplete";

type Props = {
  domain: Domain;
  disabled: boolean;
  onAnswer: (answer: Answer) => void;
  /** Без пропуска (последний уровень матча) кнопка до ввода остаётся «Ответить» и неактивна. */
  skip?: { label: ReactNode; onSkip: () => void; testId?: string } | null;
  /** Меняется — поле берёт фокус (новый раунд или уровень). */
  focusKey?: string;
};

const TEXT_MAX = 120;

export function AnswerBox({ domain, disabled, onAnswer, skip = null, focusKey }: Props) {
  const t = useTranslations();
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<Suggestion | null>(null);
  const { items } = useAutocomplete(selected ? "" : text, domain);
  const box = useRef<HTMLFormElement>(null);

  useEffect(() => {
    if (!disabled) box.current?.querySelector("input")?.focus({ preventScroll: true });
  }, [disabled, focusKey]);

  const options: ComboOption[] = items.map((s) => ({
    id: s.id,
    label: (
      <>
        <span className="min-w-0 truncate">
          <span>{s.title}</span>
          {s.subtitle && (
            <>
              {" — "}
              <b className="font-semibold text-ink">{s.subtitle}</b>
            </>
          )}
        </span>
        {s.year && <span className="shrink-0 text-[13px] text-ink-muted">{s.year}</span>}
      </>
    ),
  }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (disabled) return;
    if (selected) onAnswer({ media_item_id: selected.id });
    else {
      const value = text.trim().slice(0, TEXT_MAX);
      if (!value) return;
      onAnswer({ text: value });
    }
    setText("");
    setSelected(null);
  };

  const hasAnswer = text.trim().length > 0;
  return (
    <form ref={box} className="flex gap-2.5" onSubmit={submit}>
      <ComboField
        className="min-w-0 flex-1"
        label={t(`game.answerLabel.${domain}`)}
        placeholder={t(`game.answerPlaceholder.${domain}`)}
        clearLabel={t("game.clear")}
        value={text}
        disabled={disabled}
        options={options}
        onChange={(value) => {
          setText(value.slice(0, TEXT_MAX));
          setSelected(null);
        }}
        onSelect={(o) => {
          const s = items.find((i) => i.id === o.id);
          if (!s) return;
          setSelected(s);
          setText(s.title);
        }}
        data-testid="answer-input"
      />
      {hasAnswer || !skip ? (
        <Button
          type="submit"
          size="lg"
          disabled={disabled || !hasAnswer}
          data-testid="answer-submit"
        >
          {t("game.submit")}
        </Button>
      ) : (
        <Button
          type="button"
          size="lg"
          variant="secondary"
          disabled={disabled}
          onClick={skip.onSkip}
          data-testid={skip.testId}
        >
          {skip.label}
        </Button>
      )}
    </form>
  );
}
