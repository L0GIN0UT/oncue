"use client";
/**
 * Левый слот шапки. По умолчанию там кнопка меню; экраны внутри процесса (дуэль, комната, магазин)
 * подменяют её своей («Назад», «Сдаться», «Выйти»), отрисовав `<HeaderLeading>…</HeaderLeading>`.
 * Слот очищается, когда экран уходит.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";

const NodeContext = createContext<ReactNode>(null);
const SetterContext = createContext<Dispatch<SetStateAction<ReactNode>> | null>(null);

export function HeaderLeadingProvider({ children }: { children: ReactNode }) {
  const [node, setNode] = useState<ReactNode>(null);
  return (
    <SetterContext.Provider value={setNode}>
      <NodeContext.Provider value={node}>{children}</NodeContext.Provider>
    </SetterContext.Provider>
  );
}

/** Содержимое слота (читает шапка). */
export function useHeaderLeading(): ReactNode {
  return useContext(NodeContext);
}

/** Подменяет левую кнопку шапки на `children`, пока элемент смонтирован. */
export function HeaderLeading({ children }: { children: ReactNode }) {
  const set = useContext(SetterContext);
  useEffect(() => {
    set?.(children);
  });
  useEffect(() => () => set?.(null), [set]);
  return null;
}
