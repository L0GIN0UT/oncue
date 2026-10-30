"use client";
/**
 * Списки онлайна строками плиток (макеты «Онлайн» и «Лобби»): открытые лобби (вход в комнату) и
 * мои матчи (ссылка на итог, жалоба на игрока).
 */
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
import { ListRow } from "@/shared/ui/list-row";
import { ReportButton } from "@/features/reports/ReportDialog";

export function useModeTitle() {
  const t = useTranslations();
  const dt = useDynamicT();
  return (mode: Pick<MatchModeRef, "title_key" | "domain" | "kind">) =>
    dt(mode.title_key, `${t(`domain.${mode.domain}`)} · ${t(`online.kind.${mode.kind}`)}`);
}

const PILL =
  "inline-flex h-[30px] items-center rounded-full border-[1.5px] border-rule bg-stage px-3 text-xs font-semibold whitespace-nowrap";

/** Карточка из match (`/lobbies`) или из bff (`host_id` необязателен). */
type RoomRow = Omit<OpenRoomCard, "host_id"> & { host_id?: string | null };

export function OpenRoomList({ rooms }: { rooms: RoomRow[] }) {
  const t = useTranslations();
  const title = useModeTitle();
  if (!rooms.length) {
    return (
      <p className="py-2 text-sm text-ink-muted" data-testid="open-rooms-empty">
        {t("online.lobbies.empty")}
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-1.5">
      {rooms.map((room) => {
        const full = room.players >= room.players_max;
        return (
          <li key={room.code} data-domain={room.mode.domain} data-testid={`open-room-${room.code}`}>
            <Link
              href={`/room/${room.code}`}
              className={cn(
                "flex h-[52px] items-center gap-2.5 rounded-xl bg-stage-raised px-2.5 transition-colors hover:bg-stage-raised/70",
                full && "opacity-70",
              )}
            >
              <Avatar name={room.host_nickname} size="sm" />
              <span className="flex min-w-0 flex-1 flex-col gap-px">
                <span className="truncate text-sm font-semibold">{title(room.mode)}</span>
                <span className="truncate text-xs text-ink-muted" dir="auto">
                  {t("online.lobbies.sub", {
                    rounds: room.settings.rounds,
                    seconds: room.settings.level_window_s,
                    name: room.host_nickname,
                  })}
                </span>
              </span>
              <span className="tabular w-12 text-right font-display text-[13px] font-extrabold">
                {t("online.lobbies.slots", { n: room.players, max: room.players_max })}
              </span>
              <span className={cn(PILL, !full && "text-ink")}>
                {full ? t("online.lobbies.full") : t("online.lobbies.join")}
              </span>
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
    <ul className="flex flex-col gap-1.5">
      {matches.map((m) => (
        <li key={m.match_id} data-domain={m.mode.domain}>
          <Link
            href={`/match/${m.match_id}`}
            data-testid={`my-match-${m.match_id}`}
            className="block rounded-xl transition-opacity hover:opacity-85"
          >
            <ListRow
              lead={
                <span aria-label={t("online.myMatches.place", { place: m.place })}>{m.place}</span>
              }
              accentLead
              trailing={
                <span className="tabular">{t("online.myMatches.score", { score: m.score })}</span>
              }
              className="min-h-11"
            >
              <span className="font-semibold">{title(m.mode)}</span>
              <span className="text-xs text-ink-muted">
                {" · "}
                {m.outcome ? t(`online.outcome.${m.outcome}`) : t(`online.finish.${m.my_finish}`)}
                {" · "}
                {format.dateTime(new Date(m.finished_at), { dateStyle: "short" })}
                {" · "}
                {t("online.myMatches.players", { count: m.players })}
              </span>
            </ListRow>
          </Link>
          {m.participants && m.participants.length > 0 && (
            <ul
              className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-1"
              aria-label={t("online.myMatches.participants")}
              data-testid={`history-players-${m.match_id}`}
            >
              {m.participants.map((p) => (
                <li key={p.user_id} className="flex items-center gap-1 text-[13px]">
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
