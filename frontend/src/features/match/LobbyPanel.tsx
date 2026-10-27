"use client";
/**
 * Лобби ожидания (макет Room): код и ссылка-приглашение, места игроков с готовностью, настройки
 * строками. Хост выбирает только из `room.options` (`session.lobby.overridable` шаблона, D12) —
 * произвольных чисел нет; смена настроек сбрасывает готовность всех (это делает сервер, экран
 * только предупреждает). «Выйти» стоит в левом слоте шапки.
 */
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Check, Copy, Crown, LogOut, Play, RotateCcw, UserX } from "lucide-react";
import type { RoomView, SettingsOptions, SettingsOverride } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { SectionCap } from "@/shared/ui/section-cap";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { useModeTitle } from "@/features/online/lists";
import { fetchCollection } from "@/features/collections/api";
import { InvitePanel } from "@/features/friends/InvitePanel";
import { GameShellSlots } from "@/features/shell/game-shell-slots";
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
    <div
      className="flex flex-col gap-6 rounded-[20px] border-[1.5px] border-rule bg-stage p-5 sm:p-6"
      data-domain={room.mode.domain}
    >
      <GameShellSlots
        exit={
          me ? (
            <button
              type="button"
              onClick={() => act(commands.leave)}
              data-testid="leave-room"
              aria-label={t("match.lobby.leaveShort")}
              className="inline-flex size-11 items-center justify-center gap-2 rounded-full border-[1.5px] border-rule bg-stage text-sm font-semibold text-ink transition-colors hover:border-rule-strong sm:w-auto sm:pr-4 sm:pl-3"
            >
              <LogOut className="size-[18px]" aria-hidden />
              <span className="hidden sm:inline">{t("match.lobby.leaveShort")}</span>
            </button>
          ) : undefined
        }
        modeLabel={modeTitle(room.mode)}
      />

      <header className="flex flex-col gap-1.5 px-0.5">
        <SectionCap>{modeTitle(room.mode)}</SectionCap>
        <h1 className="font-display text-[28px] leading-[1.1] font-extrabold tracking-tight">
          {t("match.lobby.title")}
        </h1>
        <p className="text-[15px] text-ink-muted">
          {t("match.lobby.summary", {
            rounds: room.settings.rounds,
            seconds: room.settings.level_window_s,
            players: room.settings.players_max,
          })}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <span className="text-sm text-ink-muted">{t("match.lobby.code")}</span>
          <span
            className="tabular rounded-[var(--radius-control)] border-[1.5px] border-rule px-3.5 py-1.5 font-display text-xl font-extrabold tracking-[0.25em]"
            data-testid="room-code"
          >
            {room.code}
          </span>
          <Button variant="secondary" size="sm" onClick={copyLink} data-testid="copy-link">
            <Copy aria-hidden />
            {t("match.lobby.copyLink")}
          </Button>
        </div>
      </header>

      <section
        aria-label={t("match.lobby.players", {
          count: room.players.length,
          max: room.settings.players_max,
        })}
        className="flex flex-col gap-3"
      >
        <PlayersRail
          players={room.players}
          myId={myId}
          bubbles={bubbles}
          showReady
          seats={room.settings.players_max}
        />
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

      <SettingsRows
        room={room}
        options={room.options ?? null}
        editable={isHost}
        onChange={(s) => act(() => commands.settings(s))}
      />

      {(room.settings.collection_ids?.length ?? 0) > 0 && (
        <LobbyCollections ids={room.settings.collection_ids ?? []} />
      )}

      {room.start_error && !starting && <StartError code={room.start_error.code} />}

      <div className="flex gap-2.5">
        {me && (
          <Button
            size="lg"
            variant={me.ready || isHost ? "secondary" : "primary"}
            className={cn(!isHost && "flex-1")}
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
            className="flex-1"
            disabled={!allReady || !enough || starting}
            onClick={() => act(start)}
            data-testid="start-match"
          >
            <Play aria-hidden />
            {t("match.lobby.start")}
          </Button>
        )}
      </div>
      {isHost && room.memory_size > 0 && (
        <Button
          variant="quiet"
          size="sm"
          className="self-center"
          onClick={() => act(commands.resetMemory)}
          data-testid="reset-memory"
        >
          <RotateCcw aria-hidden />
          {t("match.lobby.resetMemory", { count: room.memory_size })}
        </Button>
      )}
      {isHost && (!allReady || !enough) ? (
        <p className="text-center text-[13px] text-ink-muted" data-testid="start-hint">
          {!enough
            ? t("match.lobby.needPlayers", { count: room.players_min })
            : t("match.lobby.waitReady")}
        </p>
      ) : (
        !isHost && <p className="text-center text-[13px] text-ink-muted">{t("match.lobby.hint")}</p>
      )}

      {me && <InvitePanel roomCode={room.code} />}

      <ChatPanel
        chat={room.chat}
        feed={feed}
        players={room.players}
        onEmote={commands.emote}
        onChat={commands.chat}
      />
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
      className="rounded-[var(--radius-control)] border-[1.5px] border-miss-line bg-miss-fill px-4 py-3"
      data-testid="start-error"
    >
      {t("match.lobby.startFailed", { reason: dt(key, t("match.error.generic")) })}
    </p>
  );
}

/**
 * Настройки строками: подпись слева, значение справа. Хосту поля с выбором дают список значений
 * из `options`; остальным и полям без выбора — текст.
 */
function SettingsRows({
  room,
  options,
  editable,
  onChange,
}: {
  room: RoomView;
  options: SettingsOptions | null;
  editable: boolean;
  onChange: (s: SettingsOverride) => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [pending, setPending] = useState<SettingField | null>(null);

  const label = (field: SettingField, value: string | number) => {
    if (field === "visibility")
      return t(`match.settings.visibilityValue.${value as "public" | "private"}`);
    if (field === "level_window_s") return t("match.settings.seconds", { seconds: Number(value) });
    if (field === "answer_format" || field === "clue_type")
      return dt(`match.settings.${field}Value.${value}`, String(value));
    return String(value);
  };

  return (
    <section
      aria-label={t("match.settings.title")}
      className="flex flex-col rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-1"
    >
      {FIELDS.map((field) => {
        const values = (options?.[field] ?? []) as (string | number)[];
        const current = room.settings[field];
        const choosable = editable && values.length > 1;
        return (
          <label
            key={field}
            className="flex min-h-[52px] items-center justify-between gap-3 border-b-[1.5px] border-rule py-2 text-[15px] last:border-b-0"
          >
            <span>{t(`match.settings.${field}`)}</span>
            {choosable ? (
              <select
                value={String(current)}
                disabled={pending !== null}
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
                className="h-11 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage-raised px-3 text-base text-ink disabled:opacity-70"
              >
                {values.map((v) => (
                  <option key={String(v)} value={String(v)}>
                    {label(field, v)}
                  </option>
                ))}
              </select>
            ) : values.length > 1 ? (
              // Не хост: то же поле, но заблокированное — значение читается скринридером и тестами.
              <select
                value={String(current)}
                disabled
                data-testid={`setting-${field}`}
                className="h-11 rounded-[var(--radius-control)] border-[1.5px] border-transparent bg-transparent px-3 text-base text-ink-muted"
              >
                {values.map((v) => (
                  <option key={String(v)} value={String(v)}>
                    {label(field, v)}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-ink-muted">{label(field, current)}</span>
            )}
          </label>
        );
      })}
      {editable && (
        <p className="border-t-[1.5px] border-rule py-3 text-[13px] text-ink-muted">
          {t("match.settings.resetsReady")}
        </p>
      )}
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
      <h2 id="lobby-collections" className="font-display text-base font-semibold">
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
        className="inline-flex h-[34px] items-center rounded-full border-[1.5px] border-rule px-3.5 text-sm text-ink-muted hover:border-rule-strong hover:text-ink"
        dir="auto"
      >
        {detail.data?.title ?? t("match.lobby.collectionUnknown")}
      </Link>
    </li>
  );
}
