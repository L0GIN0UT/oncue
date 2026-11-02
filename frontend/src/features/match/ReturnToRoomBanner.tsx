"use client";
/**
 * «Вернуться в комнату»: если сервер говорит, что игрок состоит в комнате (`GET /rooms/active`),
 * а он не на её экране, — плашка с возвратом и явным выходом. Устаревшее состояние (комната
 * закрыта, молчал дольше порога) сервер уже сбросил — плашки нет. Выход очищает состояние
 * сразу, навигация в другие режимы не блокируется. Выход из идущего матча засчитывается как
 * сдача, поэтому идёт через тот же диалог подтверждения, что и конфликт 409.
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { isLivePhase, useActiveRoom, useLeaveRoom } from "./active-room";
import { ConflictDialog } from "./ActiveRoomConflict";
import { matchErrorKey } from "./errors";

export function ReturnToRoomBanner() {
  const t = useTranslations();
  const dt = useDynamicT();
  const toast = useToast();
  const pathname = usePathname();
  const { room } = useActiveRoom();
  const leave = useLeaveRoom();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  if (!room || pathname === `/room/${room.code}`) return null;

  const live = isLivePhase(room.phase);
  const phaseLabel = live
    ? t("match.activeBanner.live")
    : room.phase === "summary"
      ? t("match.activeBanner.summary")
      : t("match.activeBanner.lobby");

  const out = async () => {
    setBusy(true);
    try {
      await leave(room.code);
    } catch (e) {
      toast(dt(matchErrorKey(e), t("match.error.generic")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage-raised px-4 py-3"
      data-testid="return-to-room"
    >
      <span className="flex-1">
        {t("match.activeBanner.text", { code: room.code })} · {phaseLabel}
      </span>
      <Button asChild size="sm" data-testid="return-to-room-go">
        <Link href={`/room/${room.code}`}>{t("match.activeBanner.back")}</Link>
      </Button>
      <Button
        variant="ghost"
        size="sm"
        disabled={busy}
        onClick={live ? () => setConfirming(true) : out}
        data-testid="return-to-room-leave"
      >
        {live ? t("match.activeBanner.leaveLive") : t("match.activeBanner.leave")}
      </Button>
      {confirming && (
        <ConflictDialog
          variant="leave"
          leaveLabel={t("match.activeBanner.leaveLive")}
          pending={{
            conflict: { code: room.code, phase: room.phase },
            retry: async () => undefined,
          }}
          onClose={() => setConfirming(false)}
          onRetry={async () => setConfirming(false)}
        />
      )}
    </div>
  );
}
