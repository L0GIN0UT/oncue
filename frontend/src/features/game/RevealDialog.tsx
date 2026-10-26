"use client";
/**
 * Ответ раунда (после угадывания, пропуска или последней ошибки): модалка поверх затемнённой игры.
 * Цветная шапка с исходом, картинка (кадр финальной подсказки; пока бэкенд не отдаёт обложки и
 * постеры — плашка-заглушка), «Это был», название, исполнитель или режиссёр, год, плитки «попытка»
 * и «очки», сохранение и «Продолжить» («К итогам» в последнем раунде). Пробел и Enter продолжают.
 * Данные ответа есть только у завершённого раунда (D11). У сетки (`presentation: bracket`)
 * ответа нет — модалка показывает сделанный выбор и долю согласных (`stats.agree_pct`).
 */
import { useEffect, useState, type ComponentType, type MouseEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Film, Music, Users, X } from "lucide-react";
import type { Domain, MoveStats, Presentation, RoundOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { Modal } from "@/shared/ui/modal";
import { StatTile } from "@/shared/ui/stat-tile";
import { cn } from "@/shared/lib/cn";
import { roundOutcome } from "./levels";
import { Metric } from "./Metric";
import { RevealSave } from "./RevealSave";
import { useFinalClue } from "./use-final-clue";

type Props = {
  sessionId: string;
  round: RoundOut;
  presentation: Presentation;
  stats: MoveStats | null;
  last: boolean;
  onNext: () => void;
  /** Домен сессии: определяет заглушку картинки и подпись кнопки сохранения. */
  domain: Domain;
};

type BodyProps = Pick<Props, "sessionId" | "round" | "stats" | "domain">;

/** Заливка шапки по исходу: янтарь только у «угадал», остальное — нейтральное или заливка исхода. */
const HEAD = {
  exact: "bg-music text-night",
  partial: "bg-partial-fill text-ink",
  miss: "bg-rule text-ink",
  void: "bg-rule text-ink",
} as const;

/** Удержанный Enter из поля ответа не должен тут же закрыть только что открытый ответ. */
const KEYBOARD_GUARD_MS = 300;

/** Тело по подаче итога (D12): ответ раунда или сделанный выбор. */
const bodies: Record<Presentation, ComponentType<BodyProps>> = {
  score: AnswerBody,
  streak: AnswerBody,
  bracket: ChoiceBody,
};

export function RevealDialog({
  sessionId,
  round,
  presentation,
  stats,
  last,
  onNext,
  domain,
}: Props) {
  const t = useTranslations();
  const outcome = roundOutcome(round);
  const kind =
    outcome === "exact" || outcome === "partial" || outcome === "void" ? outcome : "miss";
  const choice = presentation === "bracket";
  const Body = bodies[presentation];
  const [openedAt] = useState(() => Date.now());

  // Пробел и Enter продолжают, пока фокус не на кнопке или ссылке (там они делают своё).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== " " && e.key !== "Enter") return;
      if (e.repeat || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.target instanceof HTMLElement && e.target.closest("button, a, input, [role=menu]"))
        return;
      e.preventDefault();
      onNext();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onNext]);

  const proceed = (e: MouseEvent) => {
    if (e.detail === 0 && Date.now() - openedAt < KEYBOARD_GUARD_MS) return;
    onNext();
  };

  return (
    <Modal
      open
      onClose={onNext}
      aria-label={t("game.reveal.dialog")}
      data-testid="reveal"
      className="max-w-[440px] overflow-visible"
    >
      <div
        className={cn(
          "rounded-t-[18px] px-12 py-3.5 text-center font-display text-[17px] font-extrabold",
          choice ? HEAD.miss : HEAD[kind],
        )}
        data-testid="reveal-outcome"
      >
        {choice
          ? t("game.bracket.yourPick")
          : t(`game.reveal.${kind}`, { attempt: round.guesses.length })}
      </div>
      <div className="flex flex-col items-center gap-4 p-5">
        <Body sessionId={sessionId} round={round} stats={stats} domain={domain} />
        <div className="flex w-full gap-2.5">
          {/* «Продолжить» стоит в разметке раньше сохранения: при открытии фокус на нём. */}
          <Button
            size="lg"
            className="order-2 h-[52px] flex-1"
            onClick={proceed}
            data-testid="reveal-next"
          >
            {last ? t("game.toResults") : t("game.continue")}
          </Button>
          {round.reveal && !choice && (
            <RevealSave
              mediaItemId={round.reveal.media_item_id}
              domain={domain}
              className="order-1 h-[52px] flex-none"
            />
          )}
        </div>
        <p className="text-xs text-ink-faint">{t("game.reveal.keys")}</p>
      </div>
      <Button
        variant="icon"
        aria-label={t("common.close")}
        onClick={onNext}
        className={cn(
          "absolute top-1 right-1",
          !choice && kind === "exact" ? "text-night hover:text-night" : "text-ink hover:text-ink",
        )}
      >
        <X aria-hidden />
      </Button>
    </Modal>
  );
}

function AnswerBody({ sessionId, round, stats, domain }: BodyProps) {
  const t = useTranslations();
  const reveal = round.reveal ?? null;
  const outcome = roundOutcome(round);
  const scored = outcome === "exact" || outcome === "partial";
  if (!reveal) {
    return (
      <div className="flex flex-col items-center gap-4 text-center">
        <Art domain={domain} />
        <p className="text-ink-muted" data-testid="reveal-void">
          {t("game.reveal.voidText")}
        </p>
      </div>
    );
  }
  const people = reveal.persons.slice(0, 3).map((p) => p.name);
  const subtitle = [people.join(", "), reveal.year].filter(Boolean).join(" · ");
  return (
    <>
      <FinalArt sessionId={sessionId} round={round} domain={domain} title={reveal.title} />
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-[13px] text-ink-muted">{t("game.reveal.was")}</span>
        <h2
          className="font-display text-xl leading-tight font-medium text-balance"
          dir="auto"
          data-testid="reveal-title"
        >
          {reveal.title}
        </h2>
        {subtitle && <p className="text-[15px] text-ink-muted">{subtitle}</p>}
        {reveal.metric !== null && reveal.metric !== undefined && (
          <Metric value={reveal.metric} testId="reveal-metric" />
        )}
      </div>
      <div className="grid w-full grid-cols-2 gap-2.5">
        <StatTile
          className="items-center"
          value={t("game.reveal.attemptOf", {
            attempt: round.guesses.length,
            total: round.levels_count,
          })}
          label={t("game.reveal.attempt")}
        />
        <StatTile
          className={cn("items-center", scored && "border-partial-line")}
          accent={scored}
          value={t("game.reveal.points", { points: round.points ?? 0 })}
          label={t("game.reveal.score")}
        />
      </div>
      {stats && <AgreePct pct={stats.agree_pct} />}
    </>
  );
}

/** Сетка: ответа нет — показываем выбор игрока (подпись хода `guesses[].display`). */
function ChoiceBody({ round, stats }: BodyProps) {
  const t = useTranslations();
  const choice = round.guesses.findLast((g) => g.kind === "option")?.display ?? null;
  return (
    <>
      <div className="flex flex-col items-center gap-1 text-center">
        <span className="text-[13px] text-ink-muted">{t("game.bracket.yourPick")}</span>
        <h2
          className="font-display text-xl leading-tight font-medium text-balance"
          data-testid="reveal-choice"
        >
          {choice ?? t("game.bracket.autoPick")}
        </h2>
      </div>
      {stats && <AgreePct pct={stats.agree_pct} />}
    </>
  );
}

function AgreePct({ pct }: { pct: number }) {
  const t = useTranslations();
  const value = Math.round(pct);
  return (
    <div className="flex w-full flex-col gap-2" data-testid="agree-pct">
      <p className="flex items-center justify-center gap-2 text-[15px]">
        <Users aria-hidden className="size-[18px] text-music" />
        {t("game.agreePct", { pct: value })}
      </p>
      <span aria-hidden className="h-1.5 overflow-hidden rounded-full bg-rule">
        <span className="block h-full rounded-full bg-music" style={{ width: `${value}%` }} />
      </span>
    </div>
  );
}

/** Плашка-заглушка: обложка 1:1 у музыки, постер 2:3 у кино. */
function Art({ domain, children }: { domain: Domain; children?: ReactNode }) {
  const Icon = domain === "film" ? Film : Music;
  return (
    <div
      className={cn(
        "relative grid place-items-center overflow-hidden rounded-[var(--radius-control)] bg-stage-raised text-ink-dim",
        domain === "film" ? "h-[270px] w-[180px]" : "size-[220px]",
      )}
    >
      <Icon className="size-12" aria-hidden />
      {children}
    </div>
  );
}

/** Кадр финальной подсказки, если он есть; аудио в модалке не играет — плашка. */
function FinalArt({
  sessionId,
  round,
  domain,
  title,
}: {
  sessionId: string;
  round: RoundOut;
  domain: Domain;
  title: string;
}) {
  const t = useTranslations();
  const initial = round.reveal?.final_clue ?? null;
  const frame = initial?.kind === "image" ? initial : null;
  const { objectUrl } = useFinalClue(sessionId, round.index, frame);
  if (frame && objectUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- blob: скачанного ассета ответа раунда
      <img
        src={objectUrl}
        alt={t("game.reveal.frameAlt", { title })}
        className="aspect-video w-full rounded-[var(--radius-control)] border-[1.5px] border-rule object-cover"
        data-testid="reveal-image"
      />
    );
  }
  return (
    <Art domain={domain}>
      {frame && (
        <span className="sr-only" role="status">
          {t("game.reveal.clueLoading")}
        </span>
      )}
    </Art>
  );
}
