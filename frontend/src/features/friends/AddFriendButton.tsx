"use client";
/**
 * Кнопка «Добавить в друзья» по `user_id` (профиль игрока, итог матча): заявка уходит в social
 * без ника. Ответы на неизвестного, скрывшего заявки и заблокировавшего игрока одинаковы (404) —
 * текст ошибки их не различает.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { ErrorLine } from "./ErrorLine";
import { friendsKeys, sendRequest } from "./api";
import { useSocialAction } from "./use-action";

export function AddFriendButton({ userId, testId }: { userId: string; testId: string }) {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const [sent, setSent] = useState(false);

  if (sent) {
    return (
      <span className="text-sm text-ink-muted" role="status" data-testid={`${testId}-sent`}>
        {t("social.player.requestSent")}
      </span>
    );
  }
  const send = async () => {
    const r = await action.run(`send:${userId}`, (key) => sendRequest({ user_id: userId }, key));
    if (!r.ok) return;
    setSent(true);
    await Promise.all([
      client.invalidateQueries({ queryKey: friendsKeys.requests }),
      client.invalidateQueries({ queryKey: friendsKeys.list }),
    ]);
  };
  return (
    <>
      <Button size="sm" disabled={action.busy} onClick={() => void send()} data-testid={testId}>
        {t("social.player.addFriend")}
      </Button>
      <ErrorLine text={action.error} />
    </>
  );
}
