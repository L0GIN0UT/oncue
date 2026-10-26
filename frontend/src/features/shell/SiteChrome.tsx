"use client";
/** Верх страницы: шапка сайта, а на игровых маршрутах — оболочка игры. Держит присутствие в сети. */
import { usePathname } from "next/navigation";
import { useSessionInfo } from "@/features/auth/session";
import { PresenceHeartbeat } from "@/features/friends/use-presence";
import { GameShell } from "./GameShell";
import { isGameRoute } from "./routes";
import { SiteHeader } from "./SiteHeader";

export function SiteChrome() {
  const pathname = usePathname();
  const { data } = useSessionInfo();
  return (
    <>
      {data?.subject?.kind === "user" && <PresenceHeartbeat />}
      {isGameRoute(pathname) ? <GameShell /> : <SiteHeader />}
    </>
  );
}
