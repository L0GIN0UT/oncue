/** Контракт презентера итога (реестр по `presentation`, D12 плана этапа 3a). */
import type { ComponentType } from "react";
import type { ItemCard, ResultSummary } from "@/shared/api/types";

export type PresenterProps = {
  summary: ResultSummary;
  items: Map<string, ItemCard>;
  modeTitle: string;
};

export type Presenter = {
  /** Главная цифра итога (`final-score`): очки, серия или чемпион сетки. */
  Header: ComponentType<PresenterProps>;
  /** Разбор сессии: строки раундов или выборы сетки по турам. */
  Body: ComponentType<PresenterProps>;
};
