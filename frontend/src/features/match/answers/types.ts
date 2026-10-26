/** Контракт компонента ответа матча (реестр по `answer_format` настроек комнаты). */
import type { Domain, MatchAnswer, MyRoundView } from "@/shared/api/types";

export type MatchAnswerProps = {
  domain: Domain;
  /** Своё состояние раунда (попытки, варианты `pick`); `null` — снимок ещё не пришёл. */
  me: MyRoundView | null;
  /** Ход недоступен: уровень закрыт для меня, идёт запрос или фаза не та. */
  disabled: boolean;
  /** Можно пропустить уровень (есть следующий уровень). */
  canSkip: boolean;
  onGuess: (answer: MatchAnswer) => void;
  onSkip: () => void;
  /** Меняется при новом раунде или уровне: поле ответа берёт фокус. */
  focusKey?: string;
};
