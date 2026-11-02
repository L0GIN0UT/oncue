"use client";
/**
 * 409 `match.active_room_conflict` на вход: вместо тупика «вы уже в другой комнате» — диалог
 * «Вы уже в комнате X: выйти и продолжить?». Источник правды — сервер, код и фаза комнаты
 * приходят в `details`. «Выйти и продолжить» = `leave` + повтор того же действия; в идущем
 * матче выход засчитывается как сдача, и диалог об этом предупреждает.
 */
import { useRouter } from "next/navigation";
import { useCallback, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { Modal } from "@/shared/ui/modal";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { conflictOf, isLivePhase, useLeaveRoom, type RoomConflict } from "./active-room";
import { matchErrorKey } from "./errors";

type Pending = { conflict: RoomConflict; retry: () => Promise<void> };

/**
 * Обёртка действия, которое может упереться в чужую активную комнату.
 *
 * `guard(action)` выполняет действие; при конфликте открывает диалог и возвращает
 * `"conflict"` (вызывающий снимает «занято», ошибку не показывает), иначе `"done"`; прочие
 * ошибки пробрасывает. `dialog` — элемент, который нужно вывести рядом.
 */
export function useActiveRoomConflict(): {
  guard: (action: () => Promise<void>) => Promise<"done" | "conflict">;
  dialog: ReactNode;
} {
  const [pending, setPending] = useState<Pending | null>(null);

  const guard = useCallback(async (action: () => Promise<void>) => {
    try {
      await action();
      return "done" as const;
    } catch (e) {
      const conflict = conflictOf(e);
      if (!conflict) throw e;
      setPending({ conflict, retry: action });
      return "conflict" as const;
    }
  }, []);

  const dialog = pending ? (
    <ConflictDialog
      key={pending.conflict.code}
      pending={pending}
      onClose={() => setPending(null)}
      onRetry={async () => {
        // Диалог закрываем только после успеха: ошибка повтора (сеть, 5xx) остаётся в нём.
        // Новый конфликт (другая комната) guard покажет сам, заменив текущий диалог.
        if ((await guard(pending.retry)) === "done") setPending(null);
      }}
    />
  ) : null;

  return { guard, dialog };
}

function ConflictDialog({
  pending,
  onClose,
  onRetry,
}: {
  pending: Pending;
  onClose: () => void;
  onRetry: () => Promise<void>;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const leave = useLeaveRoom();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { code, phase } = pending.conflict;

  const leaveAndGo = async () => {
    setBusy(true);
    setError(null);
    try {
      await leave(code);
      await onRetry();
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={busy ? () => undefined : onClose}
      aria-label={t("match.conflict.title", { code })}
      data-testid="active-room-conflict"
    >
      <div className="flex flex-col gap-4 p-6">
        <h2 className="font-display text-xl font-bold">{t("match.conflict.title", { code })}</h2>
        <p className="text-ink-muted">
          {isLivePhase(phase) ? t("match.conflict.bodyLive") : t("match.conflict.bodyIdle")}
        </p>
        {error && (
          <p role="alert" className="text-miss" data-testid="conflict-error">
            {error}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={leaveAndGo} disabled={busy} data-testid="conflict-leave">
            {busy ? t("match.conflict.leaving") : t("match.conflict.leaveAndGo")}
          </Button>
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => router.push(`/room/${code}`)}
            data-testid="conflict-back"
          >
            {t("match.conflict.back")}
          </Button>
          <Button variant="ghost" disabled={busy} onClick={onClose} data-testid="conflict-cancel">
            {t("match.conflict.cancel")}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
