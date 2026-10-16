/** Контракт компонента ответа (реестр по `answer_format` режима). */
import type { Ref } from "react";
import type { Domain, LevelOut, RoundOut } from "@/shared/api/types";
import type { Move } from "../moves";

export type AnswerProps = {
  round: RoundOut;
  domain: Domain;
  /** Уровни режима (`GET /v1/modes`): подпись «Пропустить (+N с)». */
  levels: LevelOut[];
  /** Ход недоступен: раунд не активен или идёт запрос. */
  disabled: boolean;
  onMove: (move: Move) => void;
  inputRef?: Ref<HTMLInputElement>;
};
