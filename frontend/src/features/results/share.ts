/**
 * Шэр итога (D14): текст + ссылка на главную. Текст зависит от подачи итога (`presentation`):
 * у суммы очков — сетка попыток от сервера (`share_text`, при пустом клиент собирает её сам),
 * у серии — длина серии и клетки раундов, у сетки — чемпион. Названий загаданных единиц в
 * тексте нет (им можно делиться до того, как друзья сыграли); чемпион сетки — выбор игрока.
 */
import type { Presentation, ResultSummary } from "@/shared/api/types";

const CELL: Record<ResultSummary["rounds"][number]["outcome"], string> = {
  exact: "🟩",
  partial: "🟨",
  miss: "🟥",
  gave_up: "🟥",
  void: "⬜",
};

/** Перевод ключа i18n (`useTranslations()` или `createTranslator`). */
export type Translate = (key: string, values?: Record<string, string | number>) => string;

export type ShareContext = { title: string; homeUrl: string; t: Translate };

const withLink = (body: string, homeUrl: string) =>
  body.includes(homeUrl) ? body : `${body}\n${homeUrl}`;

const cells = (summary: ResultSummary) => summary.rounds.map((r) => CELL[r.outcome]).join("");

/** Сумма очков: текст сервера или сетка попыток. */
export function scoreShareText(summary: ResultSummary, { title, homeUrl }: ShareContext): string {
  const server = summary.share_text.trim();
  const body = server
    ? server
    : `${title} ${summary.score}/${summary.max_score}\n${cells(summary)}`;
  return withLink(body, homeUrl);
}

/** Серия: длина, отметка нового рекорда и клетки раундов. */
export function streakShareText(
  summary: ResultSummary,
  { title, homeUrl, t }: ShareContext,
): string {
  const lines = [title, t("results.shareText.streak", { streak: summary.score })];
  if (summary.record?.is_new) lines.push(t("results.shareText.newRecord"));
  if (summary.rounds.length) lines.push(cells(summary));
  return withLink(lines.join("\n"), homeUrl);
}

/** Сетка: чемпион игрока (без чемпиона — как у суммы очков). */
export function bracketShareText(summary: ResultSummary, ctx: ShareContext): string {
  const champion = summary.bracket?.champion?.trim();
  if (!champion) return scoreShareText(summary, ctx);
  const lines = [ctx.title, ctx.t("results.shareText.champion", { champion })];
  return withLink(lines.join("\n"), ctx.homeUrl);
}

/** Реестр шэр-текстов по подаче итога (D12). */
export const shareTextRegistry: Record<
  Presentation,
  (summary: ResultSummary, ctx: ShareContext) => string
> = {
  score: scoreShareText,
  streak: streakShareText,
  bracket: bracketShareText,
};

/** Текст для «Поделиться» по `presentation` итога. */
export function buildShareText(summary: ResultSummary, ctx: ShareContext): string {
  return shareTextRegistry[summary.presentation ?? "score"](summary, ctx);
}

export type ShareOutcome = "shared" | "copied" | "cancelled" | "failed";

type ShareEnv = {
  share?: (data: ShareData) => Promise<void>;
  canShare?: (data: ShareData) => boolean;
  writeText?: (text: string) => Promise<void>;
};

const browserEnv = (): ShareEnv => {
  const nav = globalThis.navigator as Navigator | undefined;
  return {
    share: nav?.share?.bind(nav),
    canShare: nav?.canShare?.bind(nav),
    writeText: nav?.clipboard?.writeText?.bind(nav.clipboard),
  };
};

/** Web Share API там, где он есть (мобильные), иначе — буфер обмена. */
export async function shareResult(
  text: string,
  env: ShareEnv = browserEnv(),
): Promise<ShareOutcome> {
  const data = { text };
  if (env.share && (!env.canShare || env.canShare(data))) {
    try {
      await env.share(data);
      return "shared";
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return "cancelled";
    }
  }
  if (env.writeText) {
    try {
      await env.writeText(text);
      return "copied";
    } catch {
      return "failed";
    }
  }
  return "failed";
}
