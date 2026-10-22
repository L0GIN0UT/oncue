"use client";
/**
 * Ревил раунда матча: ответ раскрывается только в фазе `reveal` (античит), финальная подсказка —
 * тем же реестром `clueRegistry`, результаты игроков — уровень угадывания и очки раунда.
 */
import { useTranslations } from "next-intl";
import type { Domain, PlayerSlot } from "@/shared/api/types";
import type { RoundRevealDelta } from "@/shared/realtime";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { SaveButton } from "@/features/library/SaveButton";
import { clueRegistry } from "@/features/game/clues/registry";
import { useLevelClue } from "./use-level-clue";

type Props = {
  code: string;
  reveal: RoundRevealDelta;
  players: PlayerSlot[];
  domain: Domain;
};

export function RoundRevealPanel({ code, reveal, players, domain }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const finalClue = useLevelClue(code, reveal.final_clue);
  const Clue = finalClue.data ? clueRegistry[finalClue.data.kind] : null;
  const names = new Map(players.map((p) => [p.user_id, p.nickname]));
  const results = [...reveal.results].sort((a, b) => b.score - a.score);

  return (
    <section
      aria-labelledby={`match-reveal-${reveal.round_index}`}
      className="flex flex-col gap-5 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
      data-testid="match-reveal"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-sm text-ink-muted">{t("match.reveal.answer")}</p>
          <h2
            id={`match-reveal-${reveal.round_index}`}
            className="font-display text-2xl leading-tight font-bold"
            dir="auto"
            data-testid="reveal-title"
          >
            {reveal.title}
          </h2>
          <p className="text-ink-muted">
            {[
              reveal.year,
              ...reveal.persons.map((p) => `${dt(`game.role.${p.role}`, p.role)}: ${p.name}`),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <SaveButton mediaItemId={reveal.media_item_id} source="reveal" domain={domain} />
      </div>
      {Clue && finalClue.data && <Clue clue={finalClue.data} scene="stage" />}
      <ul className="flex flex-col gap-1.5" aria-label={t("match.reveal.results")}>
        {results.map((r) => (
          <li key={r.user_id} className="flex items-center justify-between gap-3 text-sm">
            <span dir="auto">{names.get(r.user_id) ?? t("nav.player")}</span>
            <span className="text-ink-muted">
              {r.level === null
                ? t("match.reveal.notGuessed")
                : r.partial
                  ? t("match.reveal.partialAt", { level: r.level })
                  : t("match.reveal.guessedAt", { level: r.level })}
            </span>
            <span className="tabular font-semibold">+{r.score}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
