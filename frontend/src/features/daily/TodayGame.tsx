"use client";
/**
 * Главная `/` — сегодняшняя игра. Находит дейлик нужного домена в `pages/home` и стартует (или
 * возобновляет, или ведёт на итог) его сессию через `StartGame` и показывает игру на месте, адрес остаётся `/`. Выбор домена — из меню
 * (`/?domain=film`), на экране игры переключателя нет.
 */
import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { Domain, HomePage } from "@/shared/api/types";
import { GameScreen } from "@/features/game/GameScreen";
import { StartGame } from "@/features/game/StartGame";
import { homeQueryKey, useHomePage, useRecoverPersonalBlocks } from "./DailyCards";

export function TodayGame({ initial, domain }: { initial: HomePage | null; domain: Domain }) {
  const t = useTranslations();
  const client = useQueryClient();
  const [session, setSession] = useState<{ modeKey: string; id: string } | null>(null);
  const { data, isPending } = useHomePage(initial);
  const block = data?.blocks.dailies;
  const refresh = useCallback(() => client.invalidateQueries({ queryKey: homeQueryKey }), [client]);
  const settled = useRecoverPersonalBlocks([block?.status], refresh);

  const card = block?.status === "ok" ? block.data?.find((c) => c.domain === domain) : undefined;
  if (card && session?.modeKey === card.mode_key)
    return <GameScreen key={session.id} sessionId={session.id} />;
  if (card)
    return (
      <StartGame
        key={card.mode_key}
        modeKey={card.mode_key}
        presetId={null}
        onSession={(id) => setSession({ modeKey: card.mode_key, id })}
      />
    );

  // Блок ещё грузится или восстанавливается сессия — не показываем ошибку раньше времени.
  const waiting = isPending || (block?.status === "unavailable" && !settled);
  return (
    <p className="pt-16 text-center text-ink-muted" role={waiting ? "status" : "alert"}>
      {waiting ? t("start.loading") : t("daily.unavailable")}
    </p>
  );
}
