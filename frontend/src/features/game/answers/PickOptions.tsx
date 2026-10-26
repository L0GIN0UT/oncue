"use client";
/**
 * Формат `pick`: выбор одного из вариантов раунда (`{option_id}`). Пара «больше/меньше» —
 * опорная единица с метрикой и кнопки-подписи; сетка — две карточки с кадрами. Клавиши: `1`/`2`
 * (по порядку) и `←`/`→`. Пока ход в пути, варианты недоступны — второго выбора не будет.
 */
import { useEffect } from "react";
import { useTranslations } from "next-intl";
import type { FinalClue, ShownItem } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { Metric } from "../Metric";
import { optionForKey, pickLayout, type PickCard } from "./pick";
import type { AnswerProps } from "./types";

/** Нажатие пришло из поля ввода — клавиши выбора не перехватываем. */
const fromField = (target: EventTarget | null) =>
  target instanceof HTMLElement &&
  (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

export function PickOptions({ round, disabled, onMove }: AnswerProps) {
  const t = useTranslations();
  const { reference, cards } = pickLayout(round);
  const options = cards.map((c) => c.option);

  useEffect(() => {
    if (disabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey || e.ctrlKey || e.metaKey || fromField(e.target)) return;
      const id = optionForKey(e.key, options);
      if (!id) return;
      e.preventDefault();
      onMove({ kind: "guess", answer: { option_id: id } });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [disabled, onMove, options]);

  const withMedia = cards.some((c) => c.shown);
  return (
    <div className="flex flex-col gap-5">
      {reference.map((item) => (
        <ReferenceCard key={`${item.title}-${item.year ?? ""}`} item={item} />
      ))}
      <ul
        className={cn("grid gap-4", cards.length === 2 && "sm:grid-cols-2")}
        aria-label={t("game.optionsLabel")}
      >
        {cards.map((card, i) => (
          <li key={card.option.option_id}>
            <OptionButton
              card={card}
              index={i}
              disabled={disabled}
              media={withMedia}
              onMove={onMove}
            />
          </li>
        ))}
      </ul>
      <p className="text-sm text-ink-faint">{t("game.optionsKeys")}</p>
    </div>
  );
}

function ReferenceCard({ item }: { item: ShownItem }) {
  const t = useTranslations();
  return (
    <article
      className="flex flex-wrap items-center gap-5 rounded-[var(--radius-control)] border-[1.5px] border-rule p-4"
      data-testid="shown-item"
    >
      {item.clue && <ShownMedia clue={item.clue} title={item.title} className="w-40" />}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-sm text-ink-muted">{t("game.reference")}</p>
        <h2 className="font-display text-xl leading-tight font-bold">{item.title}</h2>
        {item.year && <p className="text-sm text-ink-muted">{item.year}</p>}
      </div>
      {item.metric !== null && item.metric !== undefined && (
        <Metric value={item.metric} testId="shown-metric" />
      )}
    </article>
  );
}

function OptionButton({
  card,
  index,
  disabled,
  media,
  onMove,
}: {
  card: PickCard;
  index: number;
  disabled: boolean;
  media: boolean;
  onMove: AnswerProps["onMove"];
}) {
  const dt = useDynamicT();
  const { option, shown } = card;
  const label =
    shown?.title ??
    option.title ??
    (option.label_key ? dt(option.label_key, option.option_id) : option.option_id);
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onMove({ kind: "guess", answer: { option_id: option.option_id } })}
      data-testid={`pick-option-${option.option_id}`}
      aria-keyshortcuts={String(index + 1)}
      className={cn(
        "group flex w-full flex-col overflow-hidden rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage text-left transition-colors hover:border-rule-strong active:translate-y-px disabled:pointer-events-none disabled:opacity-45 motion-reduce:transition-none",
        !media && "items-center justify-center",
      )}
    >
      {shown?.clue && <ShownMedia clue={shown.clue} title={shown.title} className="w-full" />}
      <span className={cn("flex items-baseline gap-3 p-4", !media && "py-5")}>
        <kbd
          aria-hidden
          className="tabular rounded-md border-[1.5px] border-rule px-2 py-0.5 font-mono text-sm text-ink-muted"
        >
          {index + 1}
        </kbd>
        <span className="font-display text-lg font-semibold">{label}</span>
        {shown?.year && <span className="text-sm text-ink-muted">{shown.year}</span>}
      </span>
    </button>
  );
}

/** Кадр открытой единицы (подписанная ссылка показывается сразу, без загадки уровня). */
function ShownMedia({
  clue,
  title,
  className,
}: {
  clue: FinalClue;
  title: string;
  className?: string;
}) {
  const t = useTranslations();
  if (clue.kind !== "image") return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка /media/, без оптимизации Next
    <img
      src={clue.url}
      alt={t("game.reveal.frameAlt", { title })}
      className={cn("aspect-video rounded-[var(--radius-control)] object-cover", className)}
      data-testid="shown-image"
    />
  );
}
