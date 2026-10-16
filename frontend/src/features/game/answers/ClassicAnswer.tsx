"use client";
/** Формат `classic`: поле ответа с автокомплитом, «Пропустить» и «Сдаться» (этап 2). */
import { useFormatter, useTranslations } from "next-intl";
import { Flag, SkipForward } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { AnswerBox } from "../AnswerBox";
import { skipGainSeconds } from "../levels";
import type { AnswerProps } from "./types";

export function ClassicAnswer({ round, domain, levels, disabled, onMove, inputRef }: AnswerProps) {
  const t = useTranslations();
  const format = useFormatter();
  const gain = skipGainSeconds(levels, round.level);
  const lastLevel = round.level >= round.levels_count;
  return (
    <>
      <AnswerBox
        domain={domain}
        disabled={disabled}
        inputRef={inputRef}
        onAnswer={(answer) => onMove({ kind: "guess", answer })}
      />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button
          variant="secondary"
          disabled={disabled}
          onClick={() => onMove({ kind: "skip" })}
          data-testid="skip"
        >
          <SkipForward aria-hidden />
          {lastLevel
            ? t("game.skipLast")
            : gain !== null
              ? t("game.skipSeconds", {
                  seconds: format.number(gain, { maximumFractionDigits: 1 }),
                })
              : t("game.skip")}
        </Button>
        <Button
          variant="ghost"
          disabled={disabled}
          onClick={() => onMove({ kind: "give_up" })}
          data-testid="give-up"
        >
          <Flag aria-hidden />
          {t("game.giveUp")}
        </Button>
      </div>
    </>
  );
}
