"use client";
/**
 * Итог сессии карточкой в виде модалки (через bff, личный блок — CSR). Оверлея нет: меню шапки
 * остаётся доступным (с главной пройденного дейлика сюда ведёт редирект). Цветная шапка («Угадал с N-й» янтарная,
 * «Не в этот раз» серая), главная цифра и разбор — по `presentation` (реестр презентеров: очки,
 * серия, чемпион сетки, D12), «Поделиться» (текст тоже по `presentation`), «Другие режимы», у гостя —
 * предложение войти. Прямая ссылка `/results/[sessionId]` выглядит так же.
 *
 * Сессия по сборнику (этап 3b, D7): `collection` итога game → название сборника в заголовке и в
 * шэре, ссылка шэра и «Сыграть ещё» ведут на страницу сборника.
 */
import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Share2 } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { ItemCard, ResultsPage } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useSessionInfo } from "@/features/auth/session";
import { presenterRegistry } from "./presenters/registry";
import { fetchCollection } from "@/features/collections/api";
import { buildShareText, shareResult, type ShareCollection, type Translate } from "./share";

/** Сборник сессии: ref из итога game, название — из collections (оба запроса не обязательны). */
function useSessionCollection(sessionId: string, enabled: boolean) {
  const ref = useQuery({
    queryKey: ["game", "result", sessionId, "collection"],
    queryFn: async () =>
      unwrap(
        await api().game.GET("/v1/sessions/{session_id}/result", {
          params: { path: { session_id: sessionId } },
        }),
      ).collection ?? null,
    enabled,
    staleTime: Infinity,
    retry: false,
  });
  const id = ref.data?.id ?? null;
  const detail = useQuery({
    queryKey: ["collections", "detail", id],
    queryFn: () => fetchCollection(id!),
    enabled: Boolean(id),
    staleTime: 60_000,
    retry: false,
  });
  return id ? { id, title: detail.data?.title ?? null } : null;
}

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
  const collection = useSessionCollection(sessionId, Boolean(data));
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
  const { Header, Body } = presenterRegistry[summary.presentation ?? "score"];

  const onShare = async () => {
    const shareCollection: ShareCollection | null = collection?.title
      ? { title: collection.title, url: `${window.location.origin}/collections/${collection.id}` }
      : null;
    const text = buildShareText(summary, {
      title: modeTitle,
      homeUrl: `${window.location.origin}/`,
      t: t as unknown as Translate,
      collection: shareCollection,
    });
    const outcome = await shareResult(text);
    if (outcome === "copied") toast(t("results.copied"));
    else if (outcome === "failed") toast(t("results.shareFailed"));
  };

  const first = summary.rounds.length === 1 ? summary.rounds[0] : null;
  const won =
    first?.outcome === "exact" ||
    (first === null &&
      (summary.score > 0 ||
        summary.rounds.some((r) => r.outcome === "exact") ||
        !!summary.bracket));
  const headline =
    first?.outcome === "exact"
      ? t("results.headline.exact", { level: first.level ?? 1 })
      : won
        ? t("results.headline.done")
        : t("results.headline.miss");

  return (
    <section
      aria-label={t("results.title")}
      data-testid="results"
      className="mx-auto mt-2 w-full max-w-[440px] animate-modal-in overflow-hidden rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage"
    >
      <div
        data-domain={domain}
        className={cn(
          "px-5 py-4 text-center font-display text-lg font-extrabold",
          won ? "bg-music text-night" : "bg-rule text-ink",
        )}
      >
        {headline}
      </div>
      <div className="flex flex-col gap-4 p-5">
        <div className="flex flex-col items-center gap-1 text-center">
          <p className="text-sm text-ink-muted">{modeTitle}</p>
          {collection?.title && (
            <p className="text-sm text-ink-muted" data-testid="results-collection">
              {t("results.collection")}{" "}
              <Link
                href={`/collections/${collection.id}`}
                dir="auto"
                className="text-ink underline-offset-4 hover:underline"
              >
                {collection.title}
              </Link>
            </p>
          )}
        </div>
        <Header summary={summary} items={items} modeTitle={modeTitle} />

        <Body summary={summary} items={items} modeTitle={modeTitle} />

        <div className="flex gap-2.5">
          <Button
            size="lg"
            variant="secondary"
            className="h-[52px] flex-1"
            onClick={onShare}
            data-testid="share"
          >
            <Share2 aria-hidden />
            {t("results.share")}
          </Button>
          <Button asChild size="lg" className="h-[52px] flex-1">
            {collection ? (
              <Link href={`/collections/${collection.id}`}>{t("results.toCollection")}</Link>
            ) : (
              <Link href="/solo">{t("results.random")}</Link>
            )}
          </Button>
        </div>

        {isGuest && (
          <aside className="flex flex-col gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule p-4">
            <p className="text-sm text-ink-muted">{t("results.guestPrompt")}</p>
            <Button asChild variant="secondary" size="sm" className="self-start">
              <Link href="/login">{t("results.guestAction")}</Link>
            </Button>
          </aside>
        )}
      </div>
    </section>
  );
}
