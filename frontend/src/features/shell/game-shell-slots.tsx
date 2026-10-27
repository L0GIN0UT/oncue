"use client";
/**
 * Слоты оболочки игры. `SiteChrome` рисует `GameShell` в раскладке, а содержимое (выход, громкость,
 * подпись режима, счёт) знают экраны игры: они отрисовывают `<GameShellSlots …/>`, шапка читает
 * значения через `useGameShellSlots`. Слоты очищаются, когда экран уходит.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import type { GameShellProps } from "./GameShell";

const ValueContext = createContext<GameShellProps>({});
const SetterContext = createContext<Dispatch<SetStateAction<GameShellProps>> | null>(null);

export function GameShellSlotsProvider({ children }: { children: React.ReactNode }) {
  const [slots, setSlots] = useState<GameShellProps>({});
  return (
    <SetterContext.Provider value={setSlots}>
      <ValueContext.Provider value={slots}>{children}</ValueContext.Provider>
    </SetterContext.Provider>
  );
}

/** Значения слотов (читает шапка игры). */
export function useGameShellSlots(): GameShellProps {
  return useContext(ValueContext);
}

/** Заполняет слоты `GameShell`, пока элемент смонтирован. */
export function GameShellSlots(props: GameShellProps) {
  const set = useContext(SetterContext);
  const { exit, volume, modeLabel, score } = props;
  useEffect(() => {
    set?.({ exit, volume, modeLabel, score });
  });
  useEffect(() => () => set?.({}), [set]);
  return null;
}
