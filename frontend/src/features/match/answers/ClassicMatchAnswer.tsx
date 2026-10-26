"use client";
/** Формат `classic` в матче: поле с автокомплитом (как в соло) и «Пропустить уровень». */
import { useTranslations } from "next-intl";
import { SkipForward } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { AnswerBox } from "@/features/game/AnswerBox";
import type { MatchAnswerProps } from "./types";

export function ClassicMatchAnswer({
  domain,
  me,
  disabled,
  canSkip,
  onGuess,
  onSkip,
  focusKey,
}: MatchAnswerProps) {
  const t = useTranslations();
  return (
    <div className="flex flex-col gap-3">
      <AnswerBox domain={domain} disabled={disabled} focusKey={focusKey} onAnswer={onGuess} />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-muted" data-testid="attempts-left" aria-live="polite">
          {me ? t("match.attemptsLeft", { count: me.attempts_left }) : " "}
        </p>
        {canSkip && (
          <Button
            variant="secondary"
            size="sm"
            disabled={disabled}
            onClick={onSkip}
            data-testid="match-skip"
          >
            <SkipForward aria-hidden />
            {t("match.skipLevel")}
          </Button>
        )}
      </div>
    </div>
  );
}
