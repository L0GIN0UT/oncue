"use client";
/**
 * Формат `classic`: поле ответа с подсказками и одна кнопка рядом — «Пропустить», пока ответа нет,
 * «Ответить», когда он есть, а на последней попытке вместо «Пропустить» красная «Сдаться».
 * На прочих уровнях «Сдаться» остаётся тихой ссылкой под полем.
 */
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
  const giveUp = () => onMove({ kind: "give_up" });
  return (
    <div className="flex flex-col gap-3">
      <AnswerBox
        domain={domain}
        disabled={disabled}
        focusKey={focusKey}
        onAnswer={(answer) => onMove({ kind: "guess", answer })}
        skip={
          lastLevel
            ? null
            : {
                testId: "skip",
                onSkip: () => onMove({ kind: "skip" }),
                // Прибавка времени слышна скринридеру; на экране — «Пропустить».
                label: (
                  <>
                    {t("game.skip")}{" "}
                    {gain !== null && (
                      <span className="sr-only">
                        (
                        {t("game.skipGain", {
                          seconds: format.number(gain, { maximumFractionDigits: 1 }),
                        })}
                        )
                      </span>
                    )}
                  </>
                ),
              }
        }
        giveUp={lastLevel ? { label: t("game.giveUp"), onGiveUp: giveUp, testId: "give-up" } : null}
      />
      {!lastLevel && (
        <Button
          variant="quiet"
          size="sm"
          className="self-center"
          disabled={disabled}
          onClick={giveUp}
          data-testid="give-up"
        >
          {t("game.giveUp")}
        </Button>
      )}
    </div>
  );
}
