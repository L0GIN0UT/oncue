"use client";
/**
 * Экран конфликта черновика (D2): черновик сохранили с другого устройства, пока здесь шли
 * правки. Слияния нет — автор выбирает «моя версия» (сохранить поверх актуальной ревизии) или
 * «с другого устройства» (взять тело сервера, свои правки отбросить).
 */
import { useEffect, useRef } from "react";
import { useFormatter, useTranslations } from "next-intl";
import type { DraftBody, DraftOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";

type Props = {
  mine: DraftBody;
  theirs: DraftOut;
  busy: boolean;
  onKeepMine: () => void;
  onTakeTheirs: () => void;
};

export function ConflictScreen({ mine, theirs, busy, onKeepMine, onTakeTheirs }: Props) {
  const t = useTranslations();
  const format = useFormatter();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const when = format.dateTime(new Date(theirs.updated_at), {
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
  return (
    <section
      role="alertdialog"
      aria-labelledby="draft-conflict-title"
      aria-describedby="draft-conflict-text"
      data-testid="draft-conflict"
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] border-[1.5px] border-partial-line bg-stage p-5"
    >
      <h2
        id="draft-conflict-title"
        ref={heading}
        tabIndex={-1}
        className="font-display text-lg font-bold outline-none"
      >
        {t("collections.conflict.title")}
      </h2>
      <p id="draft-conflict-text" className="text-ink-muted">
        {t("collections.conflict.text")}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <Version
          label={t("collections.conflict.mine")}
          title={mine.title}
          count={mine.items?.length ?? 0}
        />
        <Version
          label={t("collections.conflict.theirs", {
            device: theirs.client_label ?? t("collections.conflict.unknownDevice"),
            when,
          })}
          title={theirs.body.title}
          count={theirs.body.items?.length ?? 0}
        />
      </div>
      <div className="flex flex-wrap gap-3">
        <Button onClick={onKeepMine} disabled={busy} data-testid="draft-keep-mine">
          {t("collections.conflict.keepMine")}
        </Button>
        <Button
          variant="secondary"
          onClick={onTakeTheirs}
          disabled={busy}
          data-testid="draft-take-theirs"
        >
          {t("collections.conflict.takeTheirs")}
        </Button>
      </div>
    </section>
  );
}

function Version({ label, title, count }: { label: string; title: string; count: number }) {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-1 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-night p-4">
      <p className="text-sm text-ink-muted" dir="auto">
        {label}
      </p>
      <p className="font-medium" dir="auto">
        {title || t("collections.untitled")}
      </p>
      <p className="text-sm text-ink-muted">{t("collections.itemsCount", { count })}</p>
    </div>
  );
}
