"use client";
/**
 * Ревил завершённого раунда: исход и очки, ответ (название, год, персоны, метрика «больше/
 * меньше») и финальная подсказка — чёткий кадр или отрывок, скачанный в `Blob` (D13). Данные
 * ответа есть только у завершённого раунда (D11). У сетки (`presentation: bracket`) ответа нет —
 * ревил показывает сделанный выбор и долю согласных (`stats.agree_pct`).
 */
import { useEffect, useRef, useState, type ComponentType } from "react";
import { useTranslations } from "next-intl";
import { Pause, Play, Users } from "lucide-react";
import type { Domain, MoveStats, Presentation, RoundOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { SaveButton } from "@/features/library/SaveButton";
import { roundOutcome } from "./levels";
import { Metric } from "./Metric";
import { useFinalClue } from "./use-final-clue";

type Props = {
  sessionId: string;
  round: RoundOut;
  presentation: Presentation;
  stats: MoveStats | null;
  last: boolean;
  onNext: () => void;
  /** Домен сессии: меню «Сохранить» показывает только подходящие полки. */
  domain?: Domain | null;
};

type BodyProps = Omit<Props, "presentation" | "last" | "onNext"> & { headingId: string };

const BADGE = {
  exact: "bg-exact text-night",
  partial: "bg-partial text-night",
  miss: "bg-miss text-night",
  void: "bg-skip text-ink",
} as const;

/** Тело ревила по подаче итога (D12): ответ раунда или сделанный выбор. */
const revealBodies: Record<Presentation, ComponentType<BodyProps>> = {
  score: AnswerReveal,
  streak: AnswerReveal,
  bracket: ChoiceReveal,
};

export function RevealCard({
  sessionId,
  round,
  presentation,
  stats,
  last,
  onNext,
  domain = null,
}: Props) {
  const t = useTranslations();
  const Body = revealBodies[presentation];
  const headingId = `reveal-${round.index}`;
  return (
    <section
      aria-labelledby={headingId}
      data-testid="reveal"
      className="flex flex-col gap-6 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
    >
      <Body
        key={`${sessionId}:${round.index}`}
        sessionId={sessionId}
        round={round}
        stats={stats}
        headingId={headingId}
        domain={domain}
      />
      {stats && <AgreePct pct={stats.agree_pct} />}
      <Button size="lg" onClick={onNext} className="self-start" data-testid="reveal-next">
        {last ? t("game.toResults") : t("game.next")}
      </Button>
    </section>
  );
}

function AgreePct({ pct }: { pct: number }) {
  const t = useTranslations();
  const value = Math.round(pct);
  return (
    <div className="flex flex-col gap-2" data-testid="agree-pct">
      <p className="flex items-center gap-2 text-ink">
        <Users aria-hidden className="size-4 text-[var(--accent)]" />
        {t("game.agreePct", { pct: value })}
      </p>
      <span aria-hidden className="h-1.5 overflow-hidden rounded-full bg-rule">
        <span
          className="block h-full rounded-full bg-[var(--accent)]"
          style={{ width: `${value}%` }}
        />
      </span>
    </div>
  );
}

function useFocusHeading(index: number) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [index]);
  return heading;
}

function AnswerReveal({ sessionId, round, headingId, domain }: BodyProps) {
  const t = useTranslations();
  const outcome = roundOutcome(round);
  const kind =
    outcome === "exact" || outcome === "partial" || outcome === "void" ? outcome : "miss";
  const reveal = round.reveal ?? null;
  const heading = useFocusHeading(round.index);
  const attempt = round.guesses.length;
  return (
    <>
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
          <FinalClueView sessionId={sessionId} round={round} title={reveal.title} />
          <div className="flex flex-col gap-2">
            <h2
              id={headingId}
              ref={heading}
              tabIndex={-1}
              className="font-display text-3xl leading-tight font-bold text-balance outline-none"
            >
              {reveal.title}
            </h2>
            <p className="text-ink-muted">
              {[reveal.year, ...reveal.persons.map((p) => p.name)].filter(Boolean).join(", ")}
            </p>
            {reveal.metric !== null && reveal.metric !== undefined && (
              <Metric value={reveal.metric} testId="reveal-metric" />
            )}
            {/* После ревила id единицы известен клиенту — сохранение античит не ослабляет (D12). */}
            <SaveButton
              mediaItemId={reveal.media_item_id}
              domain={domain}
              source="reveal"
              className="mt-1 self-start"
            />
            {reveal.persons.length > 0 && (
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                {reveal.persons.map((p) => (
                  <div key={`${p.role}-${p.name}`} className="contents">
                    <dt className="text-ink-muted">{roleLabel(t, p.role)}</dt>
                    <dd>{p.name}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        </div>
      ) : (
        <h2 id={headingId} ref={heading} tabIndex={-1} className="text-ink-muted outline-none">
          {t("game.reveal.voidText")}
        </h2>
      )}
    </>
  );
}

/** Сетка: ответа нет — показываем выбор игрока (подпись хода `guesses[].display`). */
function ChoiceReveal({ round, headingId }: BodyProps) {
  const t = useTranslations();
  const heading = useFocusHeading(round.index);
  const choice = round.guesses.findLast((g) => g.kind === "option")?.display ?? null;
  return (
    <div className="flex flex-col gap-1">
      <p className="text-sm text-ink-muted">{t("game.bracket.yourPick")}</p>
      <h2
        id={headingId}
        ref={heading}
        tabIndex={-1}
        className="font-display text-3xl leading-tight font-bold text-balance outline-none"
        data-testid="reveal-choice"
      >
        {choice ?? t("game.bracket.autoPick")}
      </h2>
    </div>
  );
}

type T = ReturnType<typeof useTranslations<never>>;
function roleLabel(t: T, role: string): string {
  const key = `game.role.${role}` as Parameters<T>[0];
  return t.has(key) ? t(key) : role;
}

function FinalClueView({
  sessionId,
  round,
  title,
}: {
  sessionId: string;
  round: RoundOut;
  title: string;
}) {
  const t = useTranslations();
  const initial = round.reveal?.final_clue ?? null;
  const { objectUrl, kind, failed } = useFinalClue(sessionId, round.index, initial);
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);

  if (!objectUrl) {
    return (
      <div
        className="grid aspect-video w-full place-items-center rounded-[var(--radius-control)] bg-night text-sm text-ink-muted ring-1 ring-rule"
        role="status"
      >
        {failed ? t("game.reveal.clueFailed") : t("game.reveal.clueLoading")}
      </div>
    );
  }
  if (kind === "image") {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- blob: скачанного ассета ревила
      <img
        src={objectUrl}
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
      <audio
        ref={audio}
        src={objectUrl}
        preload="auto"
        onEnded={() => setPlaying(false)}
        data-testid="reveal-audio"
      />
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
