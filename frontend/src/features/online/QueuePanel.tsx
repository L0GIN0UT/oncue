"use client";
/**
 * Очередь рейтинговой дуэли в хабе онлайна (4b, ADR 0038): кнопки по ladder-ам и форматам из
 * блока `queues`, ожидание с heartbeat, отмена, подсказка «ищем дольше обычного», сигнал и
 * переход в комнату при найденном матче. Названия ladder-ов и форматов — из данных (i18n).
 *
 * Доступность: статусы поиска объявляются одной живой областью (без вложенных `role=status`),
 * ошибки — отдельным `role=alert`; фокус идёт за действием (отмена → в поиске, после отмены —
 * на кнопку входа, матч найден — на ссылку в комнату).
 */
import Link from "next/link";
import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Loader2, Swords } from "lucide-react";
import { Button } from "@/shared/ui/button";
import { useDynamicT } from "@/shared/i18n/dynamic";
import type { OnlineQueue, QueueFormat } from "@/shared/api/types";
import { useFormatLabel, useLadderLabel } from "@/features/rating/labels";
import { useMatchFound } from "./use-match-found";
import { useQueue } from "./use-queue";

const buttonKey = (ladder: string, format: string) => `${ladder}-${format}`;

export function QueuePanel({ queues }: { queues: OnlineQueue[] | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const ladderLabel = useLadderLabel();
  const formatLabel = useFormatLabel();
  const { state, enter, leave, dismiss } = useQueue();

  const enterButtons = useRef(new Map<string, HTMLButtonElement>());
  const lastKey = useRef<string | null>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const roomLink = useRef<HTMLAnchorElement>(null);
  const prevPhase = useRef(state.phase);

  // Фокус следует за действием игрока.
  useEffect(() => {
    const prev = prevPhase.current;
    prevPhase.current = state.phase;
    if (prev === state.phase) return;
    if (state.phase === "searching" && prev === "entering") cancelButton.current?.focus();
    else if (state.phase === "idle" && prev === "searching" && lastKey.current)
      enterButtons.current.get(lastKey.current)?.focus();
    else if (state.phase === "matched") roomLink.current?.focus();
  }, [state.phase]);

  useMatchFound(
    state.phase === "matched" ? state.roomCode : null,
    t("rating.queue.flash"),
    (code) => router.push(`/room/${code}`),
  );

  const ladders = [...new Set((queues ?? []).map((q) => q.ladder))];
  const busy = state.phase !== "idle" && state.phase !== "error";

  const start = (ladder: string, format: QueueFormat) => {
    lastKey.current = buttonKey(ladder, format);
    void enter(ladder, format);
  };

  return (
    <section aria-labelledby="online-queue" className="flex flex-col gap-4" data-testid="queue">
      <div className="flex flex-col gap-1">
        <h2 id="online-queue" className="font-display text-xl font-semibold">
          {t("rating.queue.title")}
        </h2>
        <p className="max-w-[60ch] text-sm text-ink-muted">{t("rating.queue.lead")}</p>
      </div>

      {queues === null ? (
        <p className="text-ink-muted" data-testid="queue-unavailable">
          {t("rating.queue.unavailable")}
        </p>
      ) : !queues.length ? (
        <p className="text-ink-muted">{t("rating.queue.empty")}</p>
      ) : (
        <div className="flex flex-col gap-4">
          {ladders.map((ladder) => (
            <div
              key={ladder}
              className="flex flex-col gap-2"
              data-domain={ladder}
              data-testid={`queue-ladder-${ladder}`}
            >
              <h3 className="text-sm text-[var(--accent)]">{ladderLabel(ladder)}</h3>
              <ul className="grid gap-3 sm:grid-cols-2">
                {queues
                  .filter((q) => q.ladder === ladder)
                  .map((q) => {
                    const hint = dt(`rating.formatHint.${q.format}`, "");
                    return (
                      <li key={q.format}>
                        <button
                          type="button"
                          ref={(node) => {
                            const key = buttonKey(q.ladder, q.format);
                            if (node) enterButtons.current.set(key, node);
                            else enterButtons.current.delete(key);
                          }}
                          disabled={busy}
                          onClick={() => start(q.ladder, q.format)}
                          data-testid={`queue-enter-${q.ladder}-${q.format}`}
                          className="flex h-full w-full items-start gap-3 rounded-[var(--radius-control)] bg-stage-raised p-4 text-left ring-1 ring-rule ring-inset hover:ring-2 hover:ring-[var(--accent)] focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:outline-none disabled:opacity-45"
                        >
                          <span className="grid size-9 shrink-0 place-items-center rounded-full bg-[var(--accent)] text-night">
                            <Swords className="size-4" aria-hidden />
                          </span>
                          <span className="flex flex-col gap-1">
                            <span className="font-display font-semibold">
                              {t("rating.queue.start", {
                                ladder: ladderLabel(q.ladder),
                                format: formatLabel(q.format),
                              })}
                            </span>
                            <span className="text-sm text-ink-muted">
                              {[hint, t("rating.queue.searchingCount", { count: q.searching })]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
              </ul>
            </div>
          ))}
        </div>
      )}

      <div aria-live="polite" data-testid="queue-status">
        {state.phase === "entering" && (
          <p className="text-ink-muted">{t("rating.queue.entering")}</p>
        )}
        {state.phase === "searching" && (
          <div
            className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule"
            data-testid="queue-searching"
          >
            <p className="flex items-center gap-2 font-medium">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
              {t("rating.queue.searching", {
                ladder: ladderLabel(state.ladder),
                format: formatLabel(state.format),
              })}
            </p>
            <p className="tabular text-sm text-ink-muted">
              {t("rating.queue.waited", { seconds: state.waitS })}
            </p>
            {state.slow && (
              <p className="text-sm text-ink-muted" data-testid="queue-slow">
                {t("rating.queue.slow")}
              </p>
            )}
            {state.notice && (
              <p className="text-sm text-miss" data-testid="queue-notice">
                {dt(state.notice, t("match.error.generic"))}
              </p>
            )}
            <Button
              ref={cancelButton}
              variant="secondary"
              className="self-start"
              disabled={state.leaving}
              onClick={() => void leave()}
              data-testid="queue-cancel"
            >
              {t("rating.queue.cancel")}
            </Button>
          </div>
        )}
        {state.phase === "matched" && (
          <div
            className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-5 ring-2 ring-[var(--accent)]"
            data-testid="queue-found"
          >
            <p className="font-display text-lg font-semibold">{t("rating.queue.found")}</p>
            <Button asChild className="self-start">
              <Link ref={roomLink} href={`/room/${state.roomCode}`}>
                {t("rating.queue.toRoom")}
              </Link>
            </Button>
          </div>
        )}
      </div>
      {state.phase === "error" && (
        <div className="flex flex-col gap-2" data-testid="queue-error">
          <p role="alert" className="text-miss">
            {dt(state.errorKey, t("match.error.generic"))}
          </p>
          <div className="flex gap-3">
            {state.roomCode && (
              <Button asChild variant="secondary" size="sm">
                <Link href={`/room/${state.roomCode}`}>{t("rating.queue.toRoom")}</Link>
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={dismiss}>
              {t("rating.queue.dismiss")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
