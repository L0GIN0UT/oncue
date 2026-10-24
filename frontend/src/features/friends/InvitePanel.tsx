"use client";
/**
 * «Пригласить друга» в лобби: друзья онлайн (social `GET /v1/me/friends`, статусы освежаются
 * раз в период) и кнопка приглашения в комнату. Код комнаты social не проверяет — проверка при
 * входе в match. Отказ: 403 `social.invite_forbidden` (не друг, блокировка, санкция), 429 —
 * слишком часто; повтор нажатия после сетевого сбоя уходит с тем же ключом.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { ErrorLine } from "./ErrorLine";
import { FRIENDS_REFRESH_MS } from "./config";
import { useSocialAction } from "./use-action";
import { fetchFriends, friendsKeys, inviteFriend } from "./api";

export function InvitePanel({ roomCode }: { roomCode: string }) {
  const t = useTranslations();
  const friends = useQuery({
    queryKey: friendsKeys.list,
    queryFn: fetchFriends,
    refetchInterval: FRIENDS_REFRESH_MS,
    refetchIntervalInBackground: false,
  });
  const online = friends.data?.items.filter((f) => f.status === "online") ?? [];

  return (
    <section
      aria-labelledby="invite-title"
      className="flex flex-col gap-3"
      data-testid="invite-panel"
    >
      <h2 id="invite-title" className="font-display text-xl font-semibold">
        {t("social.invite.title")}
      </h2>
      {friends.isPending ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : friends.isError ? (
        <p className="text-ink-muted">{t("social.invite.unavailable")}</p>
      ) : online.length === 0 ? (
        <p className="text-ink-muted" data-testid="invite-empty">
          {t("social.invite.none")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {online.map((f) => (
            <InviteRow
              key={f.user_id}
              userId={f.user_id}
              nickname={f.nickname}
              roomCode={roomCode}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function InviteRow({
  userId,
  nickname,
  roomCode,
}: {
  userId: string;
  nickname: string;
  roomCode: string;
}) {
  const t = useTranslations();
  const action = useSocialAction();
  const [sent, setSent] = useState(false);

  const invite = async () => {
    const r = await action.run(`invite:${userId}:${roomCode}`, (key) =>
      inviteFriend(userId, roomCode, key),
    );
    if (r.ok) setSent(true);
  };

  return (
    <li
      data-testid={`invite-${nickname}`}
      className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] bg-stage px-4 py-2 ring-1 ring-rule"
    >
      <span dir="auto" className="min-w-28 flex-1 font-medium">
        {nickname}
      </span>
      {sent ? (
        <span className="text-sm text-exact" role="status" data-testid="invite-sent">
          {t("social.invite.sent")}
        </span>
      ) : (
        <Button
          size="sm"
          variant="secondary"
          disabled={action.busy}
          onClick={() => void invite()}
          aria-label={t("social.invite.label", { name: nickname })}
          data-testid="invite-send"
        >
          {t("social.invite.send")}
        </Button>
      )}
      <div className="basis-full">
        <ErrorLine text={action.error} />
      </div>
    </li>
  );
}
