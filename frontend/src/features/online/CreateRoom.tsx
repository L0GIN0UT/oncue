"use client";
/**
 * «Создать комнату» (макеты «Онлайн» и «Лобби»): режимы комнат строками, комната создаётся
 * в `match`, затем переход на `/room/<код>`. В хабе кнопка раскрывает режимы, клик по режиму
 * сразу создаёт комнату (`instant`); на `/lobbies` режимы видны сразу, выбор подтверждает
 * кнопка «Создать». Рейтинговые комнаты — только из очереди, режимы по сборнику — со страницы
 * сборника.
 */
import { useId, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { createRoom } from "@/features/match/api";
import { matchErrorKey } from "@/features/match/errors";
import type { Domain } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { Button } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import type { OnlineModeCard } from "./bff";

const DOMAINS: Domain[] = ["music", "film"];

export function CreateRoom({
  modes,
  instant = true,
}: {
  /** `null` — блок режимов недоступен. */
  modes: OnlineModeCard[] | null;
  instant?: boolean;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const icons = useIcons();
  const panelId = useId();
  const [open, setOpen] = useState(!instant);
  const [picked, setPicked] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const Plus = icons.plus;
  const Chevron = icons.next;

  const create = async (modeKey: string) => {
    setBusy(modeKey);
    setError(null);
    try {
      const room = await createRoom({ mode_key: modeKey });
      router.push(`/room/${room.code}`);
    } catch (e) {
      setError(dt(matchErrorKey(e), t("match.error.generic")));
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col gap-2">
      {instant && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          data-testid="create-room-toggle"
          className="flex h-10 w-full items-center gap-2.5 rounded-xl bg-stage-raised px-2.5 text-left text-[13px] font-semibold transition-colors hover:bg-stage-raised/70"
        >
          <Plus aria-hidden className="size-4 text-music" />
          <span className="flex-1">{t("online.create.toggle")}</span>
          <Chevron
            aria-hidden
            className={cn("size-3.5 text-ink-faint transition-transform", open && "rotate-90")}
          />
        </button>
      )}
      {open && (
        <div id={panelId} className="flex animate-rise flex-col gap-2.5">
          {modes === null ? (
            <p className="text-sm text-ink-muted">{t("online.blockUnavailable")}</p>
          ) : !modes.length ? (
            <p className="text-sm text-ink-muted">{t("online.create.empty")}</p>
          ) : (
            DOMAINS.map((domain) => {
              const list = modes.filter(
                (m) => m.domain === domain && !m.online.ranked && !m.online.max_collections,
              );
              if (!list.length) return null;
              return (
                <div key={domain} data-domain={domain} className="flex flex-col gap-1.5">
                  <h3 className="px-0.5 text-xs text-ink-muted">{t(`domain.${domain}`)}</h3>
                  <ul className="flex flex-col gap-1.5">
                    {list.map((mode) => (
                      <li key={mode.key}>
                        <button
                          type="button"
                          disabled={busy !== null}
                          aria-pressed={instant ? undefined : picked === mode.key}
                          onClick={() => (instant ? void create(mode.key) : setPicked(mode.key))}
                          data-testid={`create-room-${mode.key}`}
                          className={cn(
                            "flex min-h-12 w-full flex-col justify-center gap-px rounded-xl border-[1.5px] bg-stage-raised px-3 py-1.5 text-left transition-colors hover:border-rule-strong disabled:opacity-45",
                            picked === mode.key && !instant ? "border-music" : "border-transparent",
                          )}
                        >
                          <span className="text-sm font-semibold">
                            {dt(mode.title_key, mode.key)}
                          </span>
                          <span className="text-xs text-ink-muted">
                            {busy === mode.key
                              ? t("online.create.busy")
                              : t("online.create.rules", {
                                  kind: t(`online.kind.${mode.online.kind}`),
                                  rounds: mode.online.rounds,
                                  min: mode.online.players_min,
                                  max: mode.online.players_max,
                                })}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })
          )}
          {!instant && modes && modes.length > 0 && (
            <Button
              disabled={picked === null || busy !== null}
              onClick={() => picked && void create(picked)}
              data-testid="create-room-submit"
              className="w-full"
            >
              {t("online.create.submit")}
            </Button>
          )}
          {error && (
            <p role="alert" className="text-sm text-miss" data-testid="create-room-error">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
