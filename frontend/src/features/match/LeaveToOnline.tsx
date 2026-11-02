"use client";
/**
 * «В онлайн» с экрана комнаты (итоги, закрытая комната, исключение): явный `leave` из любой
 * фазы, затем переход. Раньше это была простая ссылка — игрок уходил, а на сервере оставался
 * участником и упирался в 409 `active_room_conflict` при следующем входе (пункты 28, 59).
 * Если выйти не удалось (сеть), переходим всё равно, а причину показываем: «Вернуться в
 * комнату» по `GET /rooms/active` подскажет, что сервер ещё держит игрока.
 */
import { useRouter } from "next/navigation";
import { useState, type ComponentProps } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useLeaveRoom } from "./active-room";
import { matchErrorKey } from "./errors";

type Props = Pick<ComponentProps<typeof Button>, "size" | "variant"> & {
  code: string;
  "data-testid"?: string;
};

export function LeaveToOnline({ code, size, variant = "secondary", ...rest }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const toast = useToast();
  const router = useRouter();
  const leave = useLeaveRoom();
  const [busy, setBusy] = useState(false);

  const go = async () => {
    setBusy(true);
    try {
      await leave(code);
    } catch (e) {
      toast(dt(matchErrorKey(e), t("match.error.generic")));
    }
    router.push("/online");
  };

  return (
    <Button
      size={size}
      variant={variant}
      disabled={busy}
      onClick={go}
      data-testid={rest["data-testid"] ?? "leave-to-online"}
    >
      {t("match.toOnline")}
    </Button>
  );
}
