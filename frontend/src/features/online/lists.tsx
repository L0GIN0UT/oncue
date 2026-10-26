"use client";
/** Списки онлайна строками: открытые лобби (вход по коду комнаты) и мои матчи (ссылка на итог). */
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import type {
  MatchModeRef,
  MatchParticipantCard,
  MyMatchCard,
  OpenRoomCard,
} from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { Avatar } from "@/shared/ui/avatar";
import { useIcons } from "@/shared/ui/theme-provider";
import { ReportButton } from "@/features/reports/ReportDialog";

export function useModeTitle() {
  const t = useTranslations();
  const dt = useDynamicT();
  return (mode: Pick<MatchModeRef, "title_key" | "domain" | "kind">) =>
    dt(mode.title_key, `${t(`domain.${mode.domain}`)} · ${t(`online.kind.${mode.kind}`)}`);
}

const ROW =
  "flex min-h-[68px] w-full items-center gap-3.5 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-2 text-left transition-colors hover:border-rule-strong hover:bg-stage";

export function OpenRoomList({ rooms }: { rooms: OpenRoomCard[] }) {
  const t = useTranslations();
  const title = useModeTitle();
  const Next = useIcons().next;
  if (!rooms.length) {
    return (
      <p className="py-2 text-ink-muted" data-testid="open-rooms-empty">
        {t("online.lobbies.empty")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2.5">
      {rooms.map((room) => {
        const full = room.players >= room.players_max;
        return (
          <li key={room.code} data-domain={room.mode.domain} data-testid={`open-room-${room.code}`}>
            <Link href={`/room/${room.code}`} className={cn(ROW, full && "opacity-60")}>
              <Avatar name={room.host_nickname} />
              <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="truncate font-semibold">{title(room.mode)}</span>
                <span className="truncate text-[13px] text-ink-muted" dir="auto">
                  {t("online.lobbies.sub", {
                    rounds: room.settings.rounds,
                    seconds: room.settings.level_window_s,
                    name: room.host_nickname,
                  })}
                </span>
              </span>
              <span className={cn("tabular text-sm", full ? "text-ink-muted" : "text-ink-soft")}>
                {full
                  ? t("online.lobbies.full")
                  : t("online.lobbies.slots", { n: room.players, max: room.players_max })}
              </span>
              <Next aria-hidden size={18} className="shrink-0 text-ink-muted" />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

/** Карточка истории: у ответа bff есть `participants`, у прямого ответа match (`/me`) - нет. */
type HistoryCard = MyMatchCard & { participants?: MatchParticipantCard[] };

export function MyMatchList({ matches }: { matches: HistoryCard[] }) {
  const t = useTranslations();
  const format = useFormatter();
  const title = useModeTitle();
  if (!matches.length) {
    return (
      <p className="py-2 text-ink-muted" data-testid="my-matches-empty">
        {t("online.myMatches.empty")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2.5">
      {matches.map((m) => (
        <li key={m.match_id} data-domain={m.mode.domain}>
          <Link
            href={`/match/${m.match_id}`}
            className={ROW}
            data-testid={`my-match-${m.match_id}`}
          >
            <span
              className="tabular grid size-10 shrink-0 place-items-center rounded-full border-[1.5px] border-rule font-display font-bold text-music"
              aria-label={t("online.myMatches.place", { place: m.place })}
            >
              {m.place}
            </span>
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
              <span className="truncate font-semibold">{title(m.mode)}</span>
              <span className="truncate text-[13px] text-ink-muted">
                {format.dateTime(new Date(m.finished_at), {
                  dateStyle: "medium",
                  timeStyle: "short",
                })}
                {" · "}
                {t("online.myMatches.players", { count: m.players })}
                {" · "}
                {m.outcome ? t(`online.outcome.${m.outcome}`) : t(`online.finish.${m.my_finish}`)}
              </span>
            </span>
            <span className="tabular font-display text-lg font-semibold">
              {t("online.myMatches.score", { score: m.score })}
            </span>
          </Link>
          {m.participants && m.participants.length > 0 && (
            <ul
              className="flex flex-wrap items-center gap-x-4 gap-y-1 px-[18px] pt-1.5"
              aria-label={t("online.myMatches.participants")}
              data-testid={`history-players-${m.match_id}`}
            >
              {m.participants.map((p) => (
                <li key={p.user_id} className="flex items-center gap-1 text-sm">
                  <span dir="auto" className="text-ink-muted">
                    {p.nickname}
                  </span>
                  {p.can_report && (
                    <ReportButton
                      kind="player"
                      targetId={p.user_id}
                      contextMatchId={m.match_id}
                      label={t("match.rate.report")}
                      ariaLabel={t("social.report.playerLabel", { name: p.nickname })}
                      testId={`history-report-${p.nickname}`}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}
