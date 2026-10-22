"use client";
/**
 * Лобби: код и ссылка-приглашение, игроки с готовностью, настройки. Хост выбирает только из
 * `room.options` (`session.lobby.overridable` шаблона, D12) — произвольных чисел нет; смена
 * настроек сбрасывает готовность всех (это делает сервер, экран только предупреждает).
 */
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Check, Copy, Crown, LogOut, Play, RotateCcw, UserX } from "lucide-react";
import type { RoomView, SettingsOptions, SettingsOverride } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useModeTitle } from "@/features/online/lists";
import { fetchCollection } from "@/features/collections/api";
import { ChatPanel } from "./ChatPanel";
import { PlayersRail } from "./PlayersRail";
import type { FeedItem } from "./room-store";

type SettingField = keyof SettingsOverride & keyof SettingsOptions;
const FIELDS: SettingField[] = [
  "rounds",
  "level_window_s",
  "players_max",
  "answer_format",
  "clue_type",
  "visibility",
];

type Props = {
  room: RoomView;
  myId: string | null;
  bubbles: Map<string, FeedItem>;
  /** Лента чата и эмоций комнаты (чат доступен и в лобби). */
  feed: FeedItem[];
  /** Выполнить действие; ошибку показывает экран комнаты. */
  act: (action: () => Promise<unknown>) => Promise<void>;
  commands: {
    ready: (ready: boolean) => Promise<unknown>;
    settings: (settings: SettingsOverride) => Promise<unknown>;
    start: () => Promise<unknown>;
    kick: (userId: string) => Promise<unknown>;
    transferHost: (userId: string) => Promise<unknown>;
    resetMemory: () => Promise<unknown>;
    leave: () => Promise<unknown>;
    emote: (kind: "emote" | "phrase", key: string) => Promise<unknown>;
    chat: (text: string) => Promise<unknown>;
  };
};

export function LobbyPanel({ room, myId, bubbles, feed, act, commands }: Props) {
  const t = useTranslations();
  const toast = useToast();
  const modeTitle = useModeTitle();
  const me = room.players.find((p) => p.user_id === myId) ?? null;
  const isHost = !!myId && room.host_id === myId;
  const online = room.players.filter((p) => p.presence === "online");
  const allReady = online.length > 0 && online.every((p) => p.ready);
  const enough = online.length >= room.players_min;
  // Старт в пути, пока не сменилась фаза или не пришёл новый `start_error` (план game упал).
  const startErrorAt = room.start_error?.at ?? null;
  const [startPendingSince, setStartPendingSince] = useState<string | null | false>(false);
  const starting = startPendingSince !== false && startPendingSince === startErrorAt;
  const start = async () => {
    setStartPendingSince(startErrorAt);
    try {
      await commands.start();
    } catch (e) {
      setStartPendingSince(false);
      throw e;
    }
  };

  const copyLink = async () => {
    const link = `${window.location.origin}/room/${room.code}`;
    try {
      await navigator.clipboard.writeText(link);
      toast(t("match.lobby.linkCopied"));
    } catch {
      toast(link);
    }
  };

  return (
    <div className="flex flex-col gap-8" data-domain={room.mode.domain}>
      <header className="flex flex-wrap items-end justify-between gap-6">
        <div className="flex flex-col gap-2">
          <p className="text-[var(--accent)]">{modeTitle(room.mode)}</p>
          <h1 className="font-display text-2xl font-bold sm:text-3xl">{t("match.lobby.title")}</h1>
          <p className="text-sm text-ink-muted">
            {t("match.lobby.summary", {
              rounds: room.settings.rounds,
              seconds: room.settings.level_window_s,
              players: room.settings.players_max,
            })}
          </p>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <span className="text-sm text-ink-muted">{t("match.lobby.code")}</span>
          <span
            className="tabular rounded-[var(--radius-control)] bg-night px-4 py-2 font-display text-2xl font-bold tracking-[0.25em] ring-1 ring-rule"
            data-testid="room-code"
          >
            {room.code}
          </span>
          <Button variant="quiet" size="sm" onClick={copyLink} data-testid="copy-link">
            <Copy aria-hidden />
            {t("match.lobby.copyLink")}
          </Button>
        </div>
      </header>

      <section aria-labelledby="lobby-players" className="flex flex-col gap-3">
        <h2 id="lobby-players" className="font-display text-lg font-semibold">
          {t("match.lobby.players", { count: room.players.length, max: room.settings.players_max })}
        </h2>
        <PlayersRail players={room.players} myId={myId} bubbles={bubbles} showReady />
        {isHost && room.players.length > 1 && (
          <ul className="flex flex-wrap gap-2" aria-label={t("match.lobby.hostActions")}>
            {room.players
              .filter((p) => p.user_id !== myId && p.presence !== "left")
              .map((p) => (
                <li key={p.user_id} className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => act(() => commands.transferHost(p.user_id))}
                    data-testid={`transfer-${p.nickname}`}
                  >
                    <Crown aria-hidden />
                    {t("match.lobby.transfer", { name: p.nickname })}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => act(() => commands.kick(p.user_id))}
                    data-testid={`kick-${p.nickname}`}
                  >
                    <UserX aria-hidden />
                    {t("match.lobby.kick", { name: p.nickname })}
                  </Button>
                </li>
              ))}
          </ul>
        )}
      </section>

      {(room.settings.collection_ids?.length ?? 0) > 0 && (
        <LobbyCollections ids={room.settings.collection_ids ?? []} />
      )}

      <ChatPanel
        chat={room.chat}
        feed={feed}
        players={room.players}
        onEmote={commands.emote}
        onChat={commands.chat}
      />

      {room.options && (
        <SettingsForm
          room={room}
          options={room.options}
          editable={isHost}
          onChange={(s) => act(() => commands.settings(s))}
        />
      )}

      {room.start_error && !starting && <StartError code={room.start_error.code} />}

      <div className="flex flex-wrap items-center gap-3">
        {me && (
          <Button
            size="lg"
            variant={me.ready ? "secondary" : "primary"}
            onClick={() => act(() => commands.ready(!me.ready))}
            data-testid="ready-toggle"
            aria-pressed={me.ready}
          >
            <Check aria-hidden />
            {me.ready ? t("match.lobby.unready") : t("match.lobby.setReady")}
          </Button>
        )}
        {isHost && (
          <Button
            size="lg"
            disabled={!allReady || !enough || starting}
            onClick={() => act(start)}
            data-testid="start-match"
          >
            <Play aria-hidden />
            {t("match.lobby.start")}
          </Button>
        )}
        {isHost && room.memory_size > 0 && (
          <Button
            variant="ghost"
            onClick={() => act(commands.resetMemory)}
            data-testid="reset-memory"
          >
            <RotateCcw aria-hidden />
            {t("match.lobby.resetMemory", { count: room.memory_size })}
          </Button>
        )}
        <Button variant="ghost" onClick={() => act(commands.leave)} data-testid="leave-room">
          <LogOut aria-hidden />
          {t("match.lobby.leave")}
        </Button>
      </div>
      {isHost && (!allReady || !enough) && (
        <p className="text-sm text-ink-muted" data-testid="start-hint">
          {!enough
            ? t("match.lobby.needPlayers", { count: room.players_min })
            : t("match.lobby.waitReady")}
        </p>
      )}
    </div>
  );
}

/** Срыв старта (`RoomView.start_error`): понятная причина по коду `match.*`, видна всем. */
function StartError({ code }: { code: string }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const key = code.startsWith("match.") ? `match.error.${code.slice(6)}` : "match.error.generic";
  return (
    <p
      role="alert"
      className="rounded-[var(--radius-control)] bg-miss/15 px-4 py-3 text-miss ring-1 ring-miss/40"
      data-testid="start-error"
    >
      {t("match.lobby.startFailed", { reason: dt(key, t("match.error.generic")) })}
    </p>
  );
}

function SettingsForm({
  room,
  options,
  editable,
  onChange,
}: {
  room: RoomView;
  options: SettingsOptions;
  editable: boolean;
  onChange: (s: SettingsOverride) => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [pending, setPending] = useState<SettingField | null>(null);
  const fields = FIELDS.filter((f) => (options[f]?.length ?? 0) > 1);
  if (!fields.length) return null;

  const label = (field: SettingField, value: string | number) => {
    if (field === "visibility")
      return t(`match.settings.visibilityValue.${value as "public" | "private"}`);
    if (field === "level_window_s") return t("match.settings.seconds", { seconds: Number(value) });
    if (field === "answer_format" || field === "clue_type")
      return dt(`match.settings.${field}Value.${value}`, String(value));
    return String(value);
  };

  return (
    <section aria-labelledby="lobby-settings" className="flex flex-col gap-3">
      <h2 id="lobby-settings" className="font-display text-lg font-semibold">
        {t("match.settings.title")}
      </h2>
      {editable && <p className="text-sm text-ink-muted">{t("match.settings.resetsReady")}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {fields.map((field) => {
          const values = options[field] as (string | number)[];
          const current = room.settings[field];
          return (
            <label key={field} className="flex flex-col gap-1.5 text-sm">
              <span className="text-ink-muted">{t(`match.settings.${field}`)}</span>
              <select
                value={String(current)}
                disabled={!editable || pending !== null}
                onChange={async (e) => {
                  const raw = e.target.value;
                  const value = typeof values[0] === "number" ? Number(raw) : raw;
                  setPending(field);
                  try {
                    await onChange({ [field]: value } as SettingsOverride);
                  } finally {
                    setPending(null);
                  }
                }}
                data-testid={`setting-${field}`}
                className="h-11 rounded-[var(--radius-control)] bg-stage-raised px-3 text-base ring-1 ring-rule ring-inset focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-70"
              >
                {values.map((v) => (
                  <option key={String(v)} value={String(v)}>
                    {label(field, v)}
                  </option>
                ))}
              </select>
            </label>
          );
        })}
      </div>
    </section>
  );
}

/** Сборники лобби по сборнику (`settings.collection_ids`): название и ссылка на страницу. */
function LobbyCollections({ ids }: { ids: string[] }) {
  const t = useTranslations();
  return (
    <section
      aria-labelledby="lobby-collections"
      className="flex flex-col gap-2"
      data-testid="lobby-collections"
    >
      <h2 id="lobby-collections" className="font-display text-lg font-semibold">
        {t("match.lobby.collections")}
      </h2>
      <ul className="flex flex-wrap gap-2">
        {ids.map((id) => (
          <LobbyCollection key={id} id={id} />
        ))}
      </ul>
    </section>
  );
}

function LobbyCollection({ id }: { id: string }) {
  const t = useTranslations();
  const detail = useQuery({
    queryKey: ["collections", "detail", id],
    queryFn: () => fetchCollection(id),
    staleTime: 60_000,
    retry: false,
  });
  return (
    <li>
      <Link
        href={`/collections/${id}`}
        className="inline-flex rounded-full bg-stage-raised px-3 py-1.5 text-sm ring-1 ring-rule hover:ring-[var(--accent)]"
        dir="auto"
      >
        {detail.data?.title ?? t("match.lobby.collectionUnknown")}
      </Link>
    </li>
  );
}
