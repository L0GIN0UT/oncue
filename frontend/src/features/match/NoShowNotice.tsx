"use client";
/**
 * Рейтинговая пара распалась: соперник не подал сигнал за льготу (`closed {reason: no_show}`).
 * Сервер уже вернул пришедшего игрока в очередь с прежним временем ожидания, штрафа нет, поэтому
 * показываем причину и уходим в хаб онлайна: там запись очереди восстанавливает `useQueue`
 * (resume-тик) и поиск продолжается.
 */
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import { ACTIVE_ROOM_KEY } from "./active-room";

/** Сколько читать сообщение до перехода, мс (запись очереди живёт ≥ 20 с). */
export const NO_SHOW_REDIRECT_MS = 2500;

export function NoShowNotice() {
  const t = useTranslations();
  const router = useRouter();
  const client = useQueryClient();

  useEffect(() => {
    client.setQueryData(ACTIVE_ROOM_KEY, null);
    const timer = setTimeout(() => router.replace("/online"), NO_SHOW_REDIRECT_MS);
    return () => clearTimeout(timer);
  }, [client, router]);

  return (
    <div className="flex items-center gap-3 pt-8" role="status" data-testid="room-no-show">
      <Loader2 className="size-5 animate-spin text-ink-muted" aria-hidden />
      <p className="font-display text-xl font-bold">{t("match.noShow")}</p>
    </div>
  );
}
