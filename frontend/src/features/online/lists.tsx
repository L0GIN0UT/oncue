"use client";
/** Списки онлайна: открытые лобби (вход по коду комнаты) и мои матчи (ссылка на итог). */
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { Users } from "lucide-react";
import type { MatchModeRef, MyMatchCard, OpenRoomCard } from "@/shared/api/types";
import { buttonVariants } from "@/shared/ui/button";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";

export function useModeTitle() {
  const t = useTranslations();
  const dt = useDynamicT();
  return (mode: Pick<MatchModeRef, "title_key" | "domain" | "kind">) =>
    dt(mode.title_key, `${t(`domain.${mode.domain}`)} · ${t(`online.kind.${mode.kind}`)}`);
}

export function OpenRoomList({ rooms }: { rooms: OpenRoomCard[] }) {
  const t = useTranslations();
  const title = useModeTitle();
  if (!rooms.length) {
    return (
      <p className="text-ink-muted" data-testid="open-rooms-empty">
        {t("online.lobbies.empty")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-rule rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule">
      {rooms.map((room) => (
        <li
          key={room.code}
          data-domain={room.mode.domain}
          className="flex flex-wrap items-center gap-x-5 gap-y-2 px-5 py-4"
          data-testid={`open-room-${room.code}`}
        >
          <span aria-hidden className="size-2.5 shrink-0 rounded-full bg-[var(--accent)]" />
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="font-medium">{title(room.mode)}</span>
            <span className="text-sm text-ink-muted" dir="auto">
              {t("online.lobbies.host", { name: room.host_nickname })}
            </span>
          </div>
          <span className="tabular flex items-center gap-1.5 text-sm text-ink-muted">
            <Users className="size-4" aria-hidden />
            <span className="sr-only">{t("online.lobbies.players")}: </span>
            {room.players}/{room.players_max}
          </span>
          <span className="tabular text-sm text-ink-muted">
            {t("online.lobbies.settings", {
              rounds: room.settings.rounds,
              seconds: room.settings.level_window_s,
            })}
          </span>
          <Link
            href={`/room/${room.code}`}
            className={cn(buttonVariants({ variant: "secondary", size: "sm" }))}
          >
            {t("online.lobbies.enter")}
          </Link>
        </li>
      ))}
    </ul>
  );
}

export function MyMatchList({ matches }: { matches: MyMatchCard[] }) {
  const t = useTranslations();
  const format = useFormatter();
  const title = useModeTitle();
  if (!matches.length) {
    return (
      <p className="text-ink-muted" data-testid="my-matches-empty">
        {t("online.myMatches.empty")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col divide-y divide-rule rounded-[var(--radius-ticket)] bg-stage ring-1 ring-rule">
      {matches.map((m) => (
        <li key={m.match_id} data-domain={m.mode.domain}>
          <Link
            href={`/match/${m.match_id}`}
            className="flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-4 hover:bg-stage-raised focus-visible:bg-stage-raised focus-visible:outline-none"
            data-testid={`my-match-${m.match_id}`}
          >
            <span
              className="tabular grid size-10 shrink-0 place-items-center rounded-full bg-stage-raised font-display font-bold text-[var(--accent)] ring-1 ring-rule"
              aria-label={t("online.myMatches.place", { place: m.place })}
            >
              {m.place}
            </span>
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="font-medium">{title(m.mode)}</span>
              <span className="text-sm text-ink-muted">
                {format.dateTime(new Date(m.finished_at), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
                {" · "}
                {t("online.myMatches.players", { count: m.players })}
              </span>
            </span>
            <span className="text-sm text-ink-muted">
              {m.outcome ? t(`online.outcome.${m.outcome}`) : t(`online.finish.${m.my_finish}`)}
            </span>
            <span className="tabular font-display text-lg font-semibold">
              {t("online.myMatches.score", { score: m.score })}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
