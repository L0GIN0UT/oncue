"use client";
/**
 * Число непрочитанных для колокольчика (ADR 0047). Источники, от главного к страховке:
 * 1. realtime — событие `notification.created` личного канала `personal:#<user_id>` увеличивает
 *    счётчик сразу и добавляет уведомление в открытый список;
 * 2. старт — блок `bell` из bff `/v1/pages/shell` служит начальным значением счётчика: отдельного
 *    запроса `unread-count` на старте нет (если блок погас — счётчик запрашивается сам);
 * 3. страховка — опрос `unread-count` раз в 60 с. react-query не опрашивает в фоновой вкладке
 *    (`refetchIntervalInBackground: false`), а при возврате на вкладку счётчик освежается.
 * Запись в БД первична: потерянное сообщение канала догоняет опрос.
 */
import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { blockData } from "@/shared/api/blocks";
import { defaultPersonalFactory } from "@/shared/realtime/personal";
import { useMyUserId } from "@/features/auth/use-my-user-id";
import { useViewer } from "@/features/auth/session";
import { roomInviteHref } from "./invite";
import { latestInviteKey } from "./RoomInviteCard";
import { withPrepended, type ListData } from "./cache";
import {
  fetchShell,
  fetchUnreadCount,
  listQueryKey,
  shellQueryKey,
  UNREAD_POLL_MS,
  unreadQueryKey,
} from "./api";

/** Сколько последних id доставок помним для защиты от дублей. */
const SEEN_LIMIT = 200;

export function useUnreadCount(): number {
  const viewer = useViewer();
  const user = viewer.kind === "user";
  const client = useQueryClient();
  const userId = useMyUserId();

  const shell = useQuery({
    queryKey: shellQueryKey,
    queryFn: fetchShell,
    enabled: user,
    staleTime: 5 * 60_000,
    retry: false,
  });
  const bell = blockData(shell.data?.bell)?.unread;

  const unread = useQuery({
    queryKey: unreadQueryKey,
    queryFn: fetchUnreadCount,
    // Сначала shell: его число показывается, пока не пришёл точный счётчик.
    enabled: user && (shell.isFetched || shell.isError),
    // Число из shell свежо в пределах периода опроса: первый запрос счётчика — через него.
    ...(bell === undefined ? {} : { initialData: bell, initialDataUpdatedAt: shell.dataUpdatedAt }),
    staleTime: UNREAD_POLL_MS,
    refetchInterval: UNREAD_POLL_MS,
    refetchIntervalInBackground: false,
    retry: false,
  });

  // Дубли доставки одного уведомления не должны вызывать лишних запросов; набор ограничен.
  const seen = useRef(new Set<string>());
  useEffect(() => {
    if (!user || !userId) return;
    const known = seen.current;
    const transport = defaultPersonalFactory()(userId);
    transport.start({
      onNotification(item) {
        if (known.has(item.id)) return;
        known.add(item.id);
        if (known.size > SEEN_LIMIT) known.delete(known.values().next().value as string);
        // Число не накручиваем вслепую: сервер — источник истины (запись в БД первична).
        // Само уведомление сразу попадает в открытый список.
        client.setQueryData<ListData>(listQueryKey, (data) =>
          data ? withPrepended(data, item) : data,
        );
        void client.invalidateQueries({ queryKey: unreadQueryKey });
        // Приглашение в комнату — карточка с кнопкой «Войти» у колокольчика (5c).
        if (roomInviteHref(item)) client.setQueryData(latestInviteKey, item);
      },
    });
    return () => {
      transport.stop();
      known.clear();
    };
  }, [user, userId, client]);

  return user ? (unread.data ?? bell ?? 0) : 0;
}
