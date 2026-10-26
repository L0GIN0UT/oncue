"use client";
/** Формат `classic` в матче: поле с подсказками (как в соло), «Пропустить» рядом и остаток попыток. */
import { useTranslations } from "next-intl";
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
    <div className="flex flex-col gap-2.5">
      <AnswerBox
        domain={domain}
        disabled={disabled}
        focusKey={focusKey}
        onAnswer={onGuess}
        skip={canSkip ? { label: t("game.skip"), onSkip, testId: "match-skip" } : null}
      />
      <p
        className="text-center text-[13px] text-ink-muted"
        data-testid="attempts-left"
        aria-live="polite"
      >
        {me ? t("match.attemptsLeft", { count: me.attempts_left }) : " "}
      </p>
    </div>
  );
}
