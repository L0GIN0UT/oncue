"use client";
/**
 * Ревил раунда матча: ответ раскрывается только в фазе `reveal` (античит), финальная подсказка —
 * тем же реестром `clueRegistry`, результаты игроков — уровень угадывания и очки раунда.
 */
import { useTranslations } from "next-intl";
import type { Domain, PlayerSlot } from "@/shared/api/types";
import type { RoundRevealDelta } from "@/shared/realtime";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { RevealSave } from "@/features/game/RevealSave";
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
      className="flex flex-col gap-4 rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage p-5"
      data-testid="match-reveal"
    >
      <div className="flex flex-col items-center gap-1 text-center">
        <p className="text-[13px] text-ink-muted">{t("match.reveal.answer")}</p>
        <h2
          id={`match-reveal-${reveal.round_index}`}
          className="font-display text-xl leading-tight font-medium"
          dir="auto"
          data-testid="reveal-title"
        >
          {reveal.title}
        </h2>
        <p className="text-[15px] text-ink-muted">
          {[
            reveal.year,
            ...reveal.persons.map((p) => `${dt(`game.role.${p.role}`, p.role)}: ${p.name}`),
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      {Clue && finalClue.data && <Clue clue={finalClue.data} scene="stage" />}
      <ul className="flex flex-col gap-2" aria-label={t("match.reveal.results")}>
        {results.map((r) => (
          <li
            key={r.user_id}
            className="flex min-h-[52px] items-center justify-between gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] text-sm"
          >
            <span dir="auto" className="min-w-0 flex-1 truncate">
              {names.get(r.user_id) ?? t("nav.player")}
            </span>
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
      <div className="flex justify-center">
        <RevealSave mediaItemId={reveal.media_item_id} domain={domain} />
      </div>
    </section>
  );
}
