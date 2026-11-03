"use client";
/**
 * Формат `classic` в матче: поле с подсказками (как в соло), «Пропустить» рядом и остаток попыток.
 * На последнем уровне вместо «Пропустить» красная «Сдаться» (`/match/skip` — «закончил раунд»).
 */
import { useTranslations } from "next-intl";
import { AnswerBox } from "@/features/game/AnswerBox";
import type { MatchAnswerProps } from "./types";

export function ClassicMatchAnswer({
  domain,
  me,
  disabled,
  canSkip,
  lastLevel,
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
        // На последнем уровне пропуск завершает раунд для меня — «Сдаться», как в соло.
        giveUp={
          lastLevel ? { label: t("game.giveUp"), onGiveUp: onSkip, testId: "match-give-up" } : null
        }
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
