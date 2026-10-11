"use client";
/**
 * Ревил завершённого раунда: исход и очки, ответ (название, год, персоны) и финальная
 * подсказка — чёткий кадр или отрывок. Данные ответа есть только у завершённого раунда (D11).
 */
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Pause, Play } from "lucide-react";
import type { RoundOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { roundOutcome } from "./levels";

type Props = { round: RoundOut; last: boolean; onNext: () => void };

const BADGE = {
  exact: "bg-exact text-night",
  partial: "bg-partial text-night",
  miss: "bg-miss text-night",
  void: "bg-skip text-ink",
} as const;

export function RevealCard({ round, last, onNext }: Props) {
  const t = useTranslations();
  const outcome = roundOutcome(round);
  const kind =
    outcome === "exact" || outcome === "partial" || outcome === "void" ? outcome : "miss";
  const reveal = round.reveal ?? null;
  const heading = useRef<HTMLHeadingElement>(null);
  const attempt = round.guesses.length;

  useEffect(() => heading.current?.focus(), [round.index]);

  return (
    <section
      aria-labelledby={`reveal-${round.index}`}
      data-testid="reveal"
      className="flex flex-col gap-6 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
    >
      <p className="flex flex-wrap items-center gap-3">
        <span
          className={cn("rounded-full px-3 py-1 text-sm font-semibold", BADGE[kind])}
          data-testid="reveal-outcome"
        >
          {t(`game.reveal.${kind}`, { attempt })}
        </span>
        {kind !== "void" && (
          <span className="tabular font-display text-lg font-semibold">
            {t("game.reveal.points", { points: round.points ?? 0 })}
          </span>
        )}
      </p>

      {reveal ? (
        <div className="grid gap-6 sm:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] sm:items-center">
          <FinalClue clue={reveal.final_clue} title={reveal.title} />
          <div className="flex flex-col gap-2">
            <h2
              id={`reveal-${round.index}`}
              ref={heading}
              tabIndex={-1}
              className="font-display text-3xl leading-tight font-bold text-balance outline-none"
            >
              {reveal.title}
            </h2>
            <p className="text-ink-muted">
              {[reveal.year, ...reveal.persons.map((p) => p.name)].filter(Boolean).join(", ")}
            </p>
            {reveal.persons.length > 0 && (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {reveal.persons.map((p) => (
                  <div key={`${p.role}-${p.name}`} className="contents">
                    <dt className="text-ink-faint">{roleLabel(t, p.role)}</dt>
                    <dd>{p.name}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </div>
      ) : (
        <h2
          id={`reveal-${round.index}`}
          ref={heading}
          tabIndex={-1}
          className="text-ink-muted outline-none"
        >
          {t("game.reveal.voidText")}
        </h2>
      )}

      <Button size="lg" onClick={onNext} className="self-start" data-testid="reveal-next">
        {last ? t("game.toResults") : t("game.next")}
      </Button>
    </section>
  );
}

type T = ReturnType<typeof useTranslations>;
function roleLabel(t: T, role: string): string {
  const key = `game.role.${role}` as Parameters<T>[0];
  return t.has(key) ? t(key) : role;
}

function FinalClue({
  clue,
  title,
}: {
  clue: NonNullable<RoundOut["reveal"]>["final_clue"];
  title: string;
}) {
  const t = useTranslations();
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  if (clue.kind === "image") {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка /media/, без оптимизации Next
      <img
        src={clue.url}
        alt={t("game.reveal.frameAlt", { title })}
        className="aspect-video w-full rounded-[var(--radius-control)] object-cover ring-1 ring-rule"
        data-testid="reveal-image"
      />
    );
  }
  const toggle = async () => {
    const el = audio.current;
    if (!el) return;
    if (playing) {
      el.pause();
      setPlaying(false);
      return;
    }
    try {
      await el.play();
      setPlaying(true);
    } catch {
      setPlaying(false);
    }
  };
  return (
    <div className="flex items-center gap-4 rounded-[var(--radius-control)] bg-night p-4 ring-1 ring-rule">
      <audio ref={audio} src={clue.url} preload="none" onEnded={() => setPlaying(false)} />
      <button
        type="button"
        onClick={toggle}
        aria-pressed={playing}
        className="grid size-14 shrink-0 place-items-center rounded-full bg-[var(--accent)] text-night"
      >
        {playing ? (
          <Pause className="size-6" aria-hidden />
        ) : (
          <Play className="size-6 translate-x-px" aria-hidden />
        )}
        <span className="sr-only">{playing ? t("game.pause") : t("game.reveal.listen")}</span>
      </button>
      <span className="text-ink-muted">{t("game.reveal.listen")}</span>
    </div>
  );
}
