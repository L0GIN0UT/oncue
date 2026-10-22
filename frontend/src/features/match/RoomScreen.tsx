"use client";
/**
 * Экран комнаты `/room/[code]` (D2 плана этапа 4): вход по коду или ссылке, лобби, матч, итог.
 *
 * Участник (снимок `GET /state` отвечает 200) сразу подключается к комнате — так же
 * возвращаются после обрыва и закрытой вкладки. Не участник видит превью комнаты
 * (`GET /rooms/{code}`) и кнопку входа. Все команды — с `op_id`, команды фазы — с
 * `expected_version`; ошибки — по коду (`match.*`), после 409 — ресинк.
 */
import Link from "next/link";
import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { RefreshCw, WifiOff } from "lucide-react";
import { isApiError } from "@/shared/api/errors";
import type { MatchAnswer, RoomView, SettingsOverride } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useNow } from "@/features/daily/use-now";
import { useModeTitle } from "@/features/online/lists";
import { OnlineGate } from "@/features/online/OnlineGate";
import { fetchRoom, fetchRoomState, useMyUserId } from "./api";
import { isRoomCode, normalizeRoomCode } from "./code";
import { sendCommand } from "./commands";
import { matchErrorKey } from "./errors";
import { LobbyPanel } from "./LobbyPanel";
import { MatchStage, type MatchCommands } from "./MatchStage";
import { MatchSummaryView } from "./MatchSummaryView";
import { PlayersRail } from "./PlayersRail";
import type { FeedItem, RoomStore } from "./room-store";
import { useReleaseLevelClues } from "./use-level-clue";
import { useRoom, visibleVersion } from "./use-room";

/** Пузырь эмоции у места игрока живёт столько, мс. */
const BUBBLE_MS = 5_000;

export function RoomScreen({ code: raw }: { code: string }) {
  const code = normalizeRoomCode(raw);
  return <OnlineGate>{isRoomCode(code) ? <RoomBody code={code} /> : <RoomMissing />}</OnlineGate>;
}

type Entry = { member: true } | { member: false; room: RoomView };

function RoomBody({ code }: { code: string }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const myId = useMyUserId();
  const [joined, setJoined] = useState(false);
  const entry = useQuery({
    queryKey: ["room-entry", code],
    queryFn: async (): Promise<Entry> => {
      try {
        await fetchRoomState(code);
        return { member: true };
      } catch (e) {
        if (!isApiError(e, "match.not_participant")) throw e;
        return { member: false, room: await fetchRoom(code) };
      }
    },
    retry: false,
    staleTime: Infinity,
  });
  const member = entry.data?.member === true || joined;

  if (entry.isPending) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }
  if (entry.isError) {
    if (isApiError(entry.error) && entry.error.status === 404) return <RoomMissing />;
    return (
      <div className="flex flex-col items-start gap-4 pt-12">
        <p role="alert">{dt(matchErrorKey(entry.error), t("match.error.generic"))}</p>
        <Button variant="secondary" onClick={() => void entry.refetch()}>
          {t("common.retry")}
        </Button>
      </div>
    );
  }
  if (!member && entry.data.member === false) {
    return <RoomPreview room={entry.data.room} onJoined={() => setJoined(true)} myId={myId} />;
  }
  return <RoomLive code={code} myId={myId} />;
}

function RoomMissing() {
  const t = useTranslations();
  return (
    <div className="flex flex-col items-start gap-4 pt-12" data-testid="room-missing">
      <h1 className="font-display text-2xl font-bold">{t("match.error.room_not_found")}</h1>
      <Button asChild variant="secondary">
        <Link href="/online">{t("match.toOnline")}</Link>
      </Button>
    </div>
  );
}

function RoomPreview({
  room,
  onJoined,
  myId,
}: {
  room: RoomView;
  onJoined: () => void;
  myId: string | null;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const modeTitle = useModeTitle();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const full =
    room.players.filter((p) => p.presence !== "left").length >= room.settings.players_max;

  const join = async () => {
    setBusy(true);
    setError(null);
    try {
      await sendCommand(room.code, "join", {});
      onJoined();
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(false);
    }
  };

  return (
    <div
      className="flex flex-col gap-6 pt-6 sm:pt-10"
      data-domain={room.mode.domain}
      data-testid="room-preview"
    >
      <header className="flex flex-col gap-2">
        <p className="text-[var(--accent)]">{modeTitle(room.mode)}</p>
        <h1 className="font-display text-3xl font-bold">
          {t("match.preview.title", { code: room.code })}
        </h1>
        <p className="text-ink-muted">
          {t("match.lobby.summary", {
            rounds: room.settings.rounds,
            seconds: room.settings.level_window_s,
            players: room.settings.players_max,
          })}
        </p>
      </header>
      <PlayersRail players={room.players} myId={myId} />
      {room.phase !== "lobby" ? (
        <p className="text-ink-muted" role="status">
          {t("match.error.match_in_progress")}
        </p>
      ) : (
        <Button
          size="lg"
          className="self-start"
          disabled={busy || full}
          onClick={join}
          data-testid="join-room"
        >
          {full ? t("match.error.room_full") : t("match.preview.join")}
        </Button>
      )}
      {error && (
        <p role="alert" className="text-miss" data-testid="join-error">
          {error}
        </p>
      )}
    </div>
  );
}

function useBubbles(store: RoomStore, serverNow: () => number): Map<string, FeedItem> {
  const tick = useNow();
  return useMemo(() => {
    const out = new Map<string, FeedItem>();
    const now = tick ? serverNow() : 0;
    for (const item of store.feed) {
      if (item.kind !== "emote") continue;
      const at = Date.parse(item.at);
      if (now && now - at < BUBBLE_MS) out.set(item.user_id, item);
    }
    return out;
  }, [store.feed, tick, serverNow]);
}

function RoomLive({ code, myId }: { code: string; myId: string | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const toast = useToast();
  const router = useRouter();
  const { store, status, fatal, clock, command, resync } = useRoom(code, { enabled: true });
  useReleaseLevelClues(code);
  const serverNow = useCallback(() => clock.now(), [clock]);
  const bubbles = useBubbles(store, serverNow);
  const state = store.state;

  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      try {
        await action();
      } catch (e) {
        toast(dt(matchErrorKey(e), t("match.error.generic")));
        if (isApiError(e) && e.status === 409) resync();
      }
    },
    [toast, dt, t, resync],
  );

  const version = () => visibleVersion(store);
  const roundTarget = () => ({
    expected_version: version(),
    round_index: state?.match?.round_index ?? 0,
    level: state?.match?.level ?? 1,
  });

  const lobbyCommands = {
    ready: (ready: boolean) => command("ready", { ready }),
    settings: (settings: SettingsOverride) => command("settings", { settings }),
    start: () => command("start", { expected_version: version() }),
    kick: (user_id: string) => command("kick", { user_id }),
    transferHost: (user_id: string) => command("transfer-host", { user_id }),
    resetMemory: () => command("reset-memory", {}),
    leave: async () => {
      await command("leave", {});
      router.push("/online");
    },
  };
  const matchCommands: MatchCommands = {
    guess: (answer: MatchAnswer) => command("match/guess", { ...roundTarget(), answer }),
    skip: () => command("match/skip", roundTarget()),
    forfeit: () => command("match/forfeit", {}),
    emote: (kind, key) => command("match/emote", { kind, key }),
    chat: async (text) => {
      // Ошибку чата (отказ denylist) показывает сама панель чата — пробрасываем.
      return command("match/chat", { text });
    },
  };

  if (fatal) {
    const kicked = isApiError(fatal, "match.not_participant") || isApiError(fatal, "match.kicked");
    return (
      <div className="flex flex-col items-start gap-4 pt-12" data-testid="room-fatal">
        <h1 className="font-display text-2xl font-bold">
          {kicked ? t("match.error.kicked") : t("match.error.room_not_found")}
        </h1>
        <Button asChild variant="secondary">
          <Link href="/online">{t("match.toOnline")}</Link>
        </Button>
      </div>
    );
  }

  if (!state) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }

  const { room } = state;
  const matchId = store.summary?.match_id ?? room.match_id ?? null;
  const showSummary = room.phase === "summary" || (room.phase === "closed" && !!matchId);
  const names = new Map(room.players.map((p) => [p.user_id, p.nickname]));

  return (
    <div className="flex flex-col gap-6 pt-4 sm:pt-8">
      {status.offline && (
        <div
          role="alert"
          className="flex items-center gap-3 rounded-[var(--radius-control)] bg-miss/15 px-4 py-3 text-miss ring-1 ring-miss/40"
          data-testid="offline-banner"
        >
          <WifiOff className="size-5 shrink-0" aria-hidden />
          <span className="flex-1">{t("match.offline")}</span>
          <Button variant="ghost" size="sm" onClick={resync}>
            <RefreshCw aria-hidden />
            {t("common.retry")}
          </Button>
        </div>
      )}

      {room.phase === "lobby" ? (
        <LobbyPanel
          room={room}
          myId={myId}
          bubbles={bubbles}
          feed={store.feed}
          act={act}
          commands={{ ...lobbyCommands, emote: matchCommands.emote, chat: matchCommands.chat }}
        />
      ) : showSummary && matchId ? (
        <MatchSummaryView
          matchId={matchId}
          myId={myId}
          fallback={
            store.summary
              ? {
                  domain: room.mode.domain,
                  placements: store.summary.placements.map((p) => ({
                    ...p,
                    nickname: names.get(p.user_id) ?? t("nav.player"),
                  })),
                }
              : null
          }
          actions={<SummaryActions code={code} closed={room.phase === "closed"} act={act} />}
        />
      ) : room.phase === "closed" ? (
        <div className="flex flex-col items-start gap-4 pt-8" data-testid="room-closed">
          <h1 className="font-display text-2xl font-bold">{t("match.closed")}</h1>
          <Button asChild variant="secondary">
            <Link href="/online">{t("match.toOnline")}</Link>
          </Button>
        </div>
      ) : (
        <MatchStage
          code={code}
          state={state}
          store={store}
          clock={clock}
          myId={myId}
          bubbles={bubbles}
          act={act}
          commands={matchCommands}
        />
      )}
    </div>
  );
}

function SummaryActions({
  code,
  closed,
  act,
}: {
  code: string;
  closed: boolean;
  act: (action: () => Promise<unknown>) => Promise<void>;
}) {
  const t = useTranslations();
  const router = useRouter();
  const [voted, setVoted] = useState(false);

  const rematch = () =>
    act(async () => {
      const out = await sendCommand(code, "rematch", {});
      setVoted(true);
      if (out.kind === "done" && out.data.rematch_code)
        router.push(`/room/${out.data.rematch_code}`);
    });

  return (
    <div className="flex flex-wrap items-center gap-3">
      {!closed && (
        <Button size="lg" onClick={rematch} disabled={voted} data-testid="rematch">
          {voted ? t("match.rematch.waiting") : t("match.rematch.action")}
        </Button>
      )}
      <Button asChild size="lg" variant="secondary">
        <Link href="/online">{t("match.toOnline")}</Link>
      </Button>
    </div>
  );
}
