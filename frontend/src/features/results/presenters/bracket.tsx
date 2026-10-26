"use client";
/** Итог сетки (`presentation: bracket`): чемпион игрока и выборы по турам (финал первым). */
import { useTranslations } from "next-intl";
import { Crown } from "lucide-react";
import { stageLabel } from "@/features/game/bracket";
import type { BracketSummary } from "@/shared/api/types";
import { ResultRows } from "./common";
import type { Presenter, PresenterProps } from "./types";

function BracketHeader({ summary }: PresenterProps) {
  const t = useTranslations();
  const champion = summary.bracket?.champion ?? null;
  return (
    <div
      className="flex items-center gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule p-4"
      data-testid="final-score"
    >
      <Crown aria-hidden className="size-8 shrink-0 text-music" />
      <div className="flex min-w-0 flex-col gap-0.5">
        <p className="text-[13px] text-ink-muted">{t("results.championLabel")}</p>
        <h1
          className="font-display text-xl leading-tight font-medium text-balance"
          data-testid="bracket-champion"
        >
          {champion ?? t("results.unknownItem")}
        </h1>
      </div>
    </div>
  );
}

type Pick = BracketSummary["picks"][number];

/** Выборы, сгруппированные по турам: от финала к первому туру. */
export function picksByStage(picks: readonly Pick[]): [number, Pick[]][] {
  const groups = new Map<number, Pick[]>();
  for (const p of picks) groups.set(p.stage, [...(groups.get(p.stage) ?? []), p]);
  return [...groups.entries()].sort(([a], [b]) => a - b);
}

function BracketBody(props: PresenterProps) {
  const t = useTranslations();
  const picks = props.summary.bracket?.picks ?? [];
  if (!picks.length) return <ResultRows {...props} />;
  const label = (stage: number) => {
    const { key, ...values } = stageLabel(stage);
    return t(key, values);
  };
  return (
    <div className="flex flex-col gap-4" data-testid="bracket-picks">
      {picksByStage(picks).map(([stage, group]) => (
        <section key={stage} className="flex flex-col gap-2" aria-labelledby={`stage-${stage}`}>
          <h2 id={`stage-${stage}`} className="font-display text-base font-semibold">
            {label(stage)}
          </h2>
          <ol className="grid gap-2">
            {group.map((p, i) => (
              <li
                key={`${stage}-${i}`}
                data-testid="bracket-pick"
                className="flex flex-col rounded-[var(--radius-control)] border-[1.5px] border-rule px-4 py-3"
              >
                <span className="font-medium">{p.winner}</span>
                <span className="text-sm text-ink-muted">
                  {t("results.pickOver", { loser: p.loser })}
                </span>
              </li>
            ))}
          </ol>
        </section>
      ))}
    </div>
  );
}

export const bracketPresenter: Presenter = { Header: BracketHeader, Body: BracketBody };
