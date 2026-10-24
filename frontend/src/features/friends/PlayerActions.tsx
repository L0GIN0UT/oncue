"use client";
/**
 * Действия над игроком в его профиле (`blocks.social` bff): добавить в друзья, удалить из
 * друзей, заблокировать и снять блокировку. «Добавить» шлёт заявку по `user_id`. Жалоба на игрока
 * подаётся только из матча (`context_match_id`), в профиле её нет.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { PlayerSocialCard } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { playerQueryKey } from "@/features/progression/api";
import { AddFriendButton } from "./AddFriendButton";
import { ErrorLine } from "./ErrorLine";
import { useSocialAction } from "./use-action";
import { blockPlayer, friendsKeys, removeFriend, unblockPlayer } from "./api";

export function PlayerActions({ userId, social }: { userId: string; social: PlayerSocialCard }) {
  const t = useTranslations();
  const client = useQueryClient();
  const action = useSocialAction();
  const [confirmBlock, setConfirmBlock] = useState(false);

  const run = async (scope: string, fn: () => Promise<void>) => {
    const r = await action.run(scope, fn);
    if (!r.ok) return;
    setConfirmBlock(false);
    await Promise.all([
      client.invalidateQueries({ queryKey: playerQueryKey(userId) }),
      client.invalidateQueries({ queryKey: friendsKeys.list }),
      client.invalidateQueries({ queryKey: friendsKeys.blocks }),
    ]);
  };

  return (
    <div className="flex flex-col gap-2" data-testid="player-actions">
      <div className="flex flex-wrap items-center gap-2">
        {social.blocked_by_me ? (
          <>
            <p className="text-sm text-ink-muted" data-testid="player-blocked">
              {t("social.player.blocked")}
            </p>
            <Button
              variant="secondary"
              size="sm"
              disabled={action.busy}
              onClick={() => void run("unblock", () => unblockPlayer(userId))}
              data-testid="player-unblock"
            >
              {t("social.blocks.unblock")}
            </Button>
          </>
        ) : (
          <>
            {social.is_friend && (
              <>
                <span className="text-sm text-exact" data-testid="player-is-friend">
                  {t("social.player.isFriend")}
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={action.busy}
                  onClick={() => void run("remove", () => removeFriend(userId))}
                  data-testid="player-remove-friend"
                >
                  {t("social.friends.remove")}
                </Button>
              </>
            )}
            {!social.is_friend && social.can_send_request && (
              <AddFriendButton userId={userId} testId="player-add-friend" />
            )}
            {confirmBlock ? (
              <span className="flex items-center gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={action.busy}
                  onClick={() => void run("block", () => blockPlayer(userId))}
                  data-testid="player-block-confirm"
                >
                  {t("social.player.blockConfirm")}
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setConfirmBlock(false)}>
                  {t("social.cancel")}
                </Button>
              </span>
            ) : (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmBlock(true)}
                data-testid="player-block"
              >
                {t("social.player.block")}
              </Button>
            )}
          </>
        )}
      </div>
      <ErrorLine text={action.error} />
    </div>
  );
}
