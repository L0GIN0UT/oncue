"use client";
/**
 * Карточка приглашения в комнату рядом с колокольчиком: приходит по realtime (`room_invite`),
 * живёт, пока игрок не нажмёт «Войти» или «Закрыть». Остальные приглашения видны в списке
 * `/notifications` с той же кнопкой.
 */
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { NotificationItem } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { markRead, unreadQueryKey } from "./api";
import { roomInviteHref } from "./invite";

export const latestInviteKey = ["notifications", "latest-invite"] as const;

export function RoomInviteCard() {
  const t = useTranslations();
  const client = useQueryClient();
  const { data: item } = useQuery<NotificationItem | null>({
    queryKey: latestInviteKey,
    queryFn: () => null,
    enabled: false,
    initialData: null,
  });
  const href = item ? roomInviteHref(item) : null;
  if (!item || !href) return null;

  const dismiss = () => client.setQueryData(latestInviteKey, null);
  const join = () => {
    dismiss();
    void markRead(item.id)
      .then(() => client.invalidateQueries({ queryKey: unreadQueryKey }))
      .catch(() => undefined);
  };

  return (
    <div
      role="alert"
      data-testid="room-invite-card"
      className="fixed top-16 right-4 z-40 flex w-72 max-w-[calc(100vw-2rem)] flex-col gap-3 rounded-[var(--radius-control)] bg-stage-raised p-4 shadow-lg border-[1.5px] border-rule"
    >
      <div className="flex flex-col gap-0.5">
        <p dir="auto" className="font-semibold break-words">
          {item.title}
        </p>
        <p dir="auto" className="text-sm break-words text-ink-muted">
          {item.body}
        </p>
      </div>
      <div className="flex gap-2">
        <Button asChild size="sm">
          <Link href={href} onClick={join} data-testid="room-invite-join">
            {t("notifications.join")}
          </Link>
        </Button>
        <Button size="sm" variant="ghost" onClick={dismiss} data-testid="room-invite-dismiss">
          {t("notifications.dismiss")}
        </Button>
      </div>
    </div>
  );
}
