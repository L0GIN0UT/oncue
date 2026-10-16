"use client";
/**
 * Итог сессии (через bff, личный блок — CSR). Главная цифра и разбор — по `presentation`
 * (реестр презентеров: очки, серия, чемпион сетки, D12), затем «Поделиться» (текст тоже по
 * `presentation`), «Другие режимы», у гостя — предложение войти.
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

  return (
    <div data-domain={domain} className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex flex-col items-start gap-3">
        <p className="text-[var(--accent)]">{modeTitle}</p>
        {collection?.title && (
          <p className="text-ink-muted" data-testid="results-collection">
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
        <Header summary={summary} items={items} modeTitle={modeTitle} />
      </header>

      <Body summary={summary} items={items} modeTitle={modeTitle} />

      <div className="flex flex-wrap gap-3">
        <Button size="lg" onClick={onShare} data-testid="share">
          <Share2 aria-hidden />
          {t("results.share")}
        </Button>
        {collection ? (
          <Button asChild size="lg" variant="secondary">
            <Link href={`/collections/${collection.id}`}>{t("results.toCollection")}</Link>
          </Button>
        ) : (
          <Button asChild size="lg" variant="secondary">
            <Link href="/solo">{t("results.random")}</Link>
          </Button>
        )}
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
