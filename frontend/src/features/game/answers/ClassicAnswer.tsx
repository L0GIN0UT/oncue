"use client";
/** Формат `classic`: поле ответа с подсказками, «Пропустить» рядом и тихая «Сдаться» под ним. */
import { useFormatter, useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { AnswerBox } from "../AnswerBox";
import { skipGainSeconds } from "../levels";
import type { AnswerProps } from "./types";

export function ClassicAnswer({ round, domain, levels, disabled, onMove, focusKey }: AnswerProps) {
  const t = useTranslations();
  const format = useFormatter();
  const gain = skipGainSeconds(levels, round.level);
  const lastLevel = round.level >= round.levels_count;
  return (
    <div className="flex flex-col gap-3">
      <AnswerBox
        domain={domain}
        disabled={disabled}
        focusKey={focusKey}
        onAnswer={(answer) => onMove({ kind: "guess", answer })}
        skip={{
          testId: "skip",
          onSkip: () => onMove({ kind: "skip" }),
          // Прибавка времени и итог последнего пропуска слышит скринридер; на экране — «Пропустить».
          label: (
            <>
              {t("game.skip")}{" "}
              {lastLevel ? (
                <span className="sr-only">{t("game.skipLastHint")}</span>
              ) : (
                gain !== null && (
                  <span className="sr-only">
                    (
                    {t("game.skipGain", {
                      seconds: format.number(gain, { maximumFractionDigits: 1 }),
                    })}
                    )
                  </span>
                )
              )}
            </>
          ),
        }}
      />
      <Button
        variant="quiet"
        size="sm"
        className="self-center"
        disabled={disabled}
        onClick={() => onMove({ kind: "give_up" })}
        data-testid="give-up"
      >
        {t("game.giveUp")}
      </Button>
    </div>
  );
}
