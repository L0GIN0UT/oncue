/**
 * Реестр ответов по `answer_format` режима (D12 плана этапа 3a): `classic` — поле с
 * автокомплитом, `pick` — выбор варианта раунда. Новый формат — новая запись.
 */
import type { ComponentType } from "react";
import type { AnswerFormat } from "@/shared/api/types";
import { ClassicAnswer } from "./ClassicAnswer";
import { PickOptions } from "./PickOptions";
import type { AnswerProps } from "./types";

export const answerRegistry: Record<AnswerFormat, ComponentType<AnswerProps>> = {
  classic: ClassicAnswer,
  pick: PickOptions,
};
