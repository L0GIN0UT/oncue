/**
 * Реестр ответов матча по `answer_format` комнаты (как `answerRegistry` соло, D12 этапа 3a):
 * новый формат — новая запись, без ветвлений по ключу режима в экране матча.
 */
import type { ComponentType } from "react";
import type { AnswerFormat } from "@/shared/api/types";
import { ClassicMatchAnswer } from "./ClassicMatchAnswer";
import { PickMatchAnswer } from "./PickMatchAnswer";
import type { MatchAnswerProps } from "./types";

export const matchAnswerRegistry: Record<AnswerFormat, ComponentType<MatchAnswerProps>> = {
  classic: ClassicMatchAnswer,
  pick: PickMatchAnswer,
};

/** Формат известен клиенту (сервер может быть новее клиента). */
export function isKnownAnswerFormat(format: string): format is AnswerFormat {
  return Object.hasOwn(matchAnswerRegistry, format);
}
