/** Контракт компонента ответа (реестр по `answer_format` режима). */
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
  /** Меняется при новом раунде или уровне: поле ответа берёт фокус. */
  focusKey?: string;
};
