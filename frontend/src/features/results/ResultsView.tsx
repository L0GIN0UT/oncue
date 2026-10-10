"use client";
/**
 * Итог сессии (через bff, личный блок — CSR): счёт, строки по раундам с долей угадавших,
 * «Поделиться», «Случайный режим», у гостя — предложение войти.
 */
import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Share2 } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { ItemCard, ResultRoundCard, ResultsPage } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useToast } from "@/shared/ui/toast";
import { cn } from "@/shared/lib/cn";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useSessionInfo } from "@/features/auth/session";
import { buildShareText, shareResult } from "./share";

const OUTCOME_TONE: Record<ResultRoundCard["outcome"], string> = {
  exact: "bg-exact",
  partial: "bg-partial",
  miss: "bg-miss",
  gave_up: "bg-miss",
  void: "bg-skip",
};

export function ResultsView({ sessionId }: { sessionId: string }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const toast = useToast();
  const session = useSessionInfo();
  const { data, error } = useQuery({
    queryKey: ["page", "results", sessionId],
    queryFn: async (): Promise<ResultsPage> =>
      unwrap(
        await api().web.GET("/v1/pages/results/{session_id}", {
          params: { path: { session_id: sessionId } },
        }),
      ),
  });

  const unfinished = isApiError(error) && error.status === 409;
  useEffect(() => {
    if (unfinished) router.replace(`/play/s/${sessionId}`);
  }, [unfinished, router, sessionId]);

  if (error && !unfinished) {
    return (
      <div className="flex flex-col items-start gap-4 pt-12">
        <h1 className="font-display text-2xl font-bold">
          {t(isApiError(error) && error.status === 404 ? "results.notFound" : "results.failed")}
        </h1>
        <Button asChild variant="secondary">
          <Link href="/">{t("common.home")}</Link>
        </Button>
      </div>
    );
  }
  const result = data?.blocks.result;
  if (!data || !result) {
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  }
  if (result.status !== "ok" || !result.data) {
    return <p className="pt-12 text-ink-muted">{t("results.unavailable")}</p>;
  }

  const summary = result.data;
  const items = new Map<string, ItemCard>(
    (data.blocks.items.status === "ok" ? (data.blocks.items.data ?? []) : []).map((i) => [
      i.media_item_id,
      i,
    ]),
  );
  const domain = summary.mode_key?.split(".")[0] === "film" ? "film" : "music";
  const modeTitle = dt(`modes.${summary.mode_key ?? ""}.title`, t(`domain.${domain}`));
  const isGuest = session.data?.subject?.kind !== "user";

  const onShare = async () => {
    const text = buildShareText(summary, modeTitle, `${window.location.origin}/`);
    const outcome = await shareResult(text);
    if (outcome === "copied") toast(t("results.copied"));
    else if (outcome === "failed") toast(t("results.shareFailed"));
  };

  return (
    <div data-domain={domain} className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex flex-col gap-2">
        <p className="text-[var(--accent)]">{modeTitle}</p>
        <h1 className="flex items-baseline gap-3 font-display font-bold" data-testid="final-score">
          <span className="sr-only">{t("results.scoreLabel")}</span>
          <span className="tabular text-7xl leading-none sm:text-8xl">{summary.score}</span>
          <span className="tabular text-3xl text-ink-faint">/ {summary.max_score}</span>
        </h1>
      </header>

      <ol className="flex flex-col gap-2" aria-label={t("results.roundsLabel")}>
        {summary.rounds.map((r) => {
          const item = r.media_item_id ? items.get(r.media_item_id) : undefined;
          return (
            <li
              key={r.index}
              data-testid="result-row"
              className="grid grid-cols-[auto_1fr_auto] items-center gap-x-4 gap-y-1 rounded-xl bg-stage px-4 py-3 ring-1 ring-rule"
            >
              <span aria-hidden className={cn("size-3 rounded-full", OUTCOME_TONE[r.outcome])} />
              <div className="min-w-0">
                <p className="truncate font-medium">{item?.title ?? t("results.unknownItem")}</p>
                <p className="truncate text-sm text-ink-muted">
                  {t(`results.outcome.${r.outcome}`, { level: r.level ?? 0 })}
                  {item
                    ? `, ${[item.year, ...item.persons.map((p) => p.name)].filter(Boolean).join(", ")}`
                    : ""}
                </p>
              </div>
              <span className="tabular font-display text-lg font-semibold">+{r.points}</span>
              {r.exact_pct !== null && r.exact_pct !== undefined && (
                <div className="col-span-3 col-start-1 mt-1 flex items-center gap-3 text-xs text-ink-muted sm:col-span-2 sm:col-start-2">
                  <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-rule">
                    <span
                      className="block h-full rounded-full bg-[var(--accent)]"
                      style={{ width: `${Math.round(r.exact_pct)}%` }}
                    />
                  </span>
                  <span className="tabular">
                    {t("results.exactPct", { pct: Math.round(r.exact_pct) })}
                  </span>
                </div>
              )}
            </li>
          );
        })}
      </ol>

      <div className="flex flex-wrap gap-3">
        <Button size="lg" onClick={onShare} data-testid="share">
          <Share2 aria-hidden />
          {t("results.share")}
        </Button>
        <Button asChild size="lg" variant="secondary">
          <Link href="/solo">{t("results.random")}</Link>
        </Button>
      </div>

      {isGuest && (
        <aside className="flex flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule sm:flex-row sm:items-center sm:justify-between">
          <p className="max-w-[48ch] text-ink-muted">{t("results.guestPrompt")}</p>
          <Button asChild variant="secondary">
            <Link href="/login">{t("results.guestAction")}</Link>
          </Button>
        </aside>
      )}
    </div>
  );
}
