/**
 * Шэр итога (D14): текст-сетка как у Wordle + ссылка на главную. Текст сетки формирует сервер
 * (`share_text`); клиент только добавляет ссылку, а при пустом тексте собирает сетку сам.
 */
import type { ResultSummary } from "@/shared/api/types";

const CELL: Record<ResultSummary["rounds"][number]["outcome"], string> = {
  exact: "🟩",
  partial: "🟨",
  miss: "🟥",
  gave_up: "🟥",
  void: "⬜",
};

/** Текст для «Поделиться». `title` — название режима, `homeUrl` — адрес главной. */
export function buildShareText(summary: ResultSummary, title: string, homeUrl: string): string {
  const server = summary.share_text.trim();
  const body = server
    ? server
    : `${title} ${summary.score}/${summary.max_score}\n${summary.rounds.map((r) => CELL[r.outcome]).join("")}`;
  return body.includes(homeUrl) ? body : `${body}\n${homeUrl}`;
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
