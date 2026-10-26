"use client";
/**
 * Эмоции, готовые фразы и чат комнаты (D14, ADR 0036). Эмоции и фразы — ключи i18n из
 * конфига режима (`room.chat`), доступны везде. Свободный чат — только если `chat.free_text`
 * (кастомное лобби); текст выводится как текст (React экранирует), без разметки и ссылок.
 * Отказ сервера `match.chat_rejected` — понятная причина рядом с полем, текст не теряется.
 */
import { useId, useState, type FormEvent } from "react";
import { useTranslations } from "next-intl";
import { Send } from "lucide-react";
import type { ChatOptions, PlayerSlot } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { inputClass } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { matchErrorKey } from "./errors";
import { feedText } from "./PlayersRail";
import type { FeedItem } from "./room-store";

const CHAT_MAX = 200;

type Props = {
  chat: ChatOptions;
  feed: FeedItem[];
  players: PlayerSlot[];
  onEmote: (kind: "emote" | "phrase", key: string) => Promise<unknown>;
  onChat: (text: string) => Promise<unknown>;
};

export function ChatPanel({ chat, feed, players, onEmote, onChat }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const id = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const names = new Map(players.map((p) => [p.user_id, p.nickname]));

  const run = async (action: () => Promise<unknown>, onOk?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onOk?.();
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
    } finally {
      setBusy(false);
    }
  };

  const send = (e: FormEvent) => {
    e.preventDefault();
    const value = text.trim();
    if (!value) return;
    void run(
      () => onChat(value),
      () => setText(""),
    );
  };

  const recent = feed.slice(-20);
  return (
    <section
      aria-label={t("match.chat.title")}
      className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-4"
      data-testid="chat"
    >
      {(chat.emotes.length > 0 || chat.phrases.length > 0) && (
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("match.chat.reactions")}>
          {chat.emotes.map((key) => (
            <Button
              key={key}
              variant="secondary"
              size="sm"
              disabled={busy}
              onClick={() => void run(() => onEmote("emote", key))}
              data-testid={`emote-${key}`}
            >
              {dt(key, key)}
            </Button>
          ))}
          {chat.phrases.map((key) => (
            <Button
              key={key}
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => void run(() => onEmote("phrase", key))}
              data-testid={`phrase-${key}`}
            >
              {dt(key, key)}
            </Button>
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <ol
          className="flex max-h-48 flex-col gap-1 overflow-y-auto text-sm"
          aria-live="polite"
          data-testid="chat-feed"
        >
          {recent.map((item) => (
            <li key={`${item.version}`} className="break-words">
              <span className="font-medium text-ink" dir="auto">
                {names.get(item.user_id) ?? t("nav.player")}
              </span>
              <span className="text-ink-faint">: </span>
              <span className={item.kind === "chat" ? "text-ink" : "text-ink-muted"} dir="auto">
                {feedText(item, dt)}
              </span>
            </li>
          ))}
        </ol>
      )}

      {chat.free_text && (
        <form onSubmit={send} className="flex gap-2">
          <label htmlFor={`${id}-chat`} className="sr-only">
            {t("match.chat.label")}
          </label>
          <input
            id={`${id}-chat`}
            value={text}
            maxLength={CHAT_MAX}
            onChange={(e) => {
              setText(e.target.value);
              setError(null);
            }}
            placeholder={t("match.chat.placeholder")}
            autoComplete="off"
            aria-invalid={!!error}
            aria-describedby={error ? `${id}-error` : undefined}
            className={cn(inputClass, "h-11 min-w-0 flex-1 px-3")}
            data-testid="chat-input"
          />
          <Button type="submit" size="sm" className="h-10" disabled={busy || !text.trim()}>
            <Send aria-hidden />
            <span className="sr-only sm:not-sr-only">{t("match.chat.send")}</span>
          </Button>
        </form>
      )}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-sm text-miss" data-testid="chat-error">
          {error}
        </p>
      )}
    </section>
  );
}
