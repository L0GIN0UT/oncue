"use client";
/**
 * Итог сессии (через bff, личный блок — CSR) в оболочке игры: карточка итога (цветная шапка, счёт,
 * вкладки раундов), строки раундов с обложкой, «Поделиться», следующий шаг («Теперь кино» после
 * дейлика музыки), «На главную». Главная цифра и разбор — по `presentation` (реестр презентеров:
 * очки, серия, чемпион сетки, D12), у гостя — предложение войти. С главной пройденного дейлика
 * сюда ведёт ссылка плитки.
 *
 * Сессия по сборнику (этап 3b, D7): `collection` итога game → название сборника в заголовке и в
 * шэре, ссылка шэра и «Сыграть ещё» ведут на страницу сборника.
 */
import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { ArrowRight, Share2 } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { Domain, ItemCard, ResultsPage } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useSessionInfo } from "@/features/auth/session";
import { useHomePage } from "@/features/daily/DailyCards";
import { ExitLink } from "@/features/game/ExitLink";
import { useSessionMode } from "@/features/game/modes";
import { GameShellSlots } from "@/features/shell/game-shell-slots";
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
  const format = useFormatter();
  const session = useSessionInfo();
  const home = useHomePage(null);
  const { data, error } = useQuery({
    queryKey: ["page", "results", sessionId],
    queryFn: async (): Promise<ResultsPage> =>
      unwrap(
        await api().web.GET("/v1/pages/results/{session_id}", {
          params: { path: { session_id: sessionId } },
        }),
      ),
  });

  const summaryData = data?.blocks.result.status === "ok" ? data.blocks.result.data : null;
  const sessionDomain: Domain | undefined = summaryData
    ? summaryData.mode_key?.split(".")[0] === "film"
      ? "film"
      : "music"
    : undefined;
  const { mode } = useSessionMode(sessionDomain, summaryData?.mode_key ?? undefined);
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
  const domain = sessionDomain ?? "music";
  const modeTitle = dt(`modes.${summary.mode_key ?? ""}.title`, t(`domain.${domain}`));
  const isGuest = session.data?.subject?.kind !== "user";
  const { Header, Body } = presenterRegistry[summary.presentation ?? "score"];

  // Следующий шаг: у дейлика — дейлик другого домена, если он ещё не сыгран; у сборника — сам
  // сборник; иначе — режимы соло.
  const dailyBlock = home.data?.blocks.dailies;
  const dailies = dailyBlock?.status === "ok" ? (dailyBlock.data ?? []) : [];
  const dailyCard = dailies.find((c) => c.mode_key === summary.mode_key);
  const otherDaily = dailyCard
    ? dailies.find((c) => c.domain !== dailyCard.domain && c.status !== "finished")
    : undefined;
  const next = collection
    ? { href: `/collections/${collection.id}`, label: t("results.toCollection") }
    : otherDaily
      ? { href: `/play/${otherDaily.mode_key}`, label: t(`results.next.${otherDaily.domain}`) }
      : { href: "/solo", label: t("results.random") };
  const day = dailyCard
    ? format.dateTime(new Date(`${dailyCard.day}T12:00:00`), { day: "numeric", month: "long" })
    : null;
  const caption = [modeTitle, t(`domain.${domain}`).toLowerCase(), day, t("results.caption")]
    .filter(Boolean)
    .join(" · ");

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
  const presenterProps = { summary, items, modeTitle, domain, levels: mode?.levels ?? [] };

  return (
    <>
      <GameShellSlots exit={<ExitLink label={t("results.home")} />} modeLabel={caption} />
      <div data-testid="results" className="mx-auto mt-2 flex w-full max-w-[720px] flex-col gap-6">
        <section
          aria-label={t("results.title")}
          className="animate-modal-in overflow-hidden rounded-[var(--radius-ticket)] border-[1.5px] border-rule bg-stage"
        >
          <div
            data-domain={domain}
            className={cn(
              "px-4 py-3.5 text-center font-display text-lg font-extrabold",
              won ? "bg-music text-night" : "bg-rule text-ink",
            )}
          >
            {headline}
          </div>
          <div className="flex flex-col items-center gap-4 px-6 pt-6 pb-6">
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
            <Header {...presenterProps} />
          </div>
        </section>

        <Body {...presenterProps} />

        <div className="flex flex-wrap gap-3">
          <Button
            size="lg"
            variant="secondary"
            className="min-w-40 flex-1"
            onClick={onShare}
            data-testid="share"
          >
            <Share2 aria-hidden />
            {t("results.share")}
          </Button>
          <Button asChild size="lg" className="min-w-52 flex-[1.4]" data-testid="results-next">
            <Link href={next.href}>
              {next.label}
              <ArrowRight aria-hidden />
            </Link>
          </Button>
          <Button asChild size="lg" variant="secondary" className="min-w-40 flex-1">
            <Link href="/">{t("results.home")}</Link>
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
    </>
  );
}
