"use client";
/**
 * Страница сборника `/collections/{id}` (D3, D6, D7): карточка, режимы game «N из M» и число
 * раундов из `round_options`, старт игры, лайк и избранное (оптимистично, откат при ошибке),
 * отметка «пройден» и состав — только если сервер его прислал (`items`). Гостю и анониму лайк и
 * избранное — ссылки на регистрацию.
 */
import { useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { CheckCircle2, Heart, Pencil, Play, Star } from "lucide-react";
import { isApiError } from "@/shared/api/errors";
import type {
  CollectionDetailOut,
  CollectionModeCard,
  CollectionPageOut,
  RoundOption,
} from "@/shared/api/types";
import { Button, buttonVariants } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { SaveButton } from "@/features/library/SaveButton";
import { collectionKeys, fetchCollectionPage, setFavorite, setLike } from "../api";
import { Cover } from "../Cover";
import { apiErrorText } from "../errors";
import { useAuthorName, useTagTitle } from "./CollectionCard";

export function CollectionView({ collectionId }: { collectionId: string }) {
  const t = useTranslations();
  const page = useQuery({
    queryKey: collectionKeys.page(collectionId),
    queryFn: () => fetchCollectionPage(collectionId),
  });

  if (page.isPending)
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (page.isError) {
    const missing = isApiError(page.error) && page.error.status === 404;
    return (
      <div className="flex flex-col items-start gap-4 pt-12">
        <h1 className="font-display text-2xl font-bold">
          {t(missing ? "collections.notFound" : "collections.unavailable")}
        </h1>
        <Button asChild variant="secondary">
          <Link href="/collections">{t("collections.toCatalog")}</Link>
        </Button>
      </div>
    );
  }
  const block = page.data.blocks.collection;
  if (block.status !== "ok" || !block.data)
    return <p className="pt-12 text-ink-muted">{t("collections.unavailable")}</p>;
  return <CollectionBody page={page.data} detail={block.data} />;
}

function CollectionBody({
  page,
  detail,
}: {
  page: CollectionPageOut;
  detail: CollectionDetailOut;
}) {
  const t = useTranslations();
  const authorName = useAuthorName();
  const tagTitle = useTagTitle();
  const me = page.me ?? null;
  const items = detail.items ?? null;
  return (
    <article data-domain={detail.domain} className="flex flex-col gap-10 pt-6 sm:pt-10">
      <header className="flex flex-col gap-6 sm:flex-row sm:items-start">
        <Cover preset={detail.cover?.preset} size="lg" />
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <p className="text-sm text-[var(--accent)]">
            {t(`domain.${detail.domain}`)}
            {detail.kind === "curated" ? ` · ${t("collections.curated")}` : ""}
            {detail.featured ? ` · ${t("collections.featured")}` : ""}
          </p>
          <h1
            dir="auto"
            className="font-display text-3xl leading-tight font-bold text-balance sm:text-4xl"
          >
            {detail.title}
          </h1>
          <p className="text-ink-muted">
            {t("collections.by")}{" "}
            <span dir="auto" className="text-ink" data-testid="collection-author">
              {authorName(detail.author)}
            </span>
          </p>
          {detail.description && (
            <p dir="auto" className="max-w-[65ch] whitespace-pre-line text-ink-muted">
              {detail.description}
            </p>
          )}
          <dl className="tabular flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <Stat label={t("collections.stats.items")}>
              <span data-testid="collection-items-count">{detail.items_count}</span>
            </Stat>
            <Stat label={t("collections.stats.likes")}>
              <span data-testid="collection-likes-count">{detail.likes_count}</span>
            </Stat>
            <Stat label={t("collections.stats.plays")}>{detail.plays_count}</Stat>
            <Stat label={t("collections.stats.difficulty")}>
              {detail.difficulty
                ? t(`collections.difficulty.${detail.difficulty}`)
                : t("collections.difficulty.unknown")}
            </Stat>
          </dl>
          {detail.tags.length > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label={t("collections.tagsLabel")}>
              {detail.tags.map((k) => (
                <li
                  key={k}
                  className="rounded-full px-3 py-1 text-xs text-ink-muted ring-1 ring-rule"
                >
                  {tagTitle(k)}
                </li>
              ))}
            </ul>
          )}
          {detail.status === "hidden" && (
            <p
              className="rounded-xl bg-miss/15 px-4 py-2 text-sm text-miss ring-1 ring-miss"
              role="note"
            >
              {t("collections.hiddenByModerator")}
            </p>
          )}
          <Reactions collectionId={detail.collection_id} me={me} />
        </div>
      </header>

      <ModesBlock page={page} detail={detail} />

      {items && (
        <section
          aria-labelledby="collection-items-title"
          className="flex flex-col gap-4"
          data-testid="collection-items"
        >
          <h2 id="collection-items-title" className="font-display text-xl font-bold">
            {t("collections.items")}
          </h2>
          <ol className="flex flex-col gap-2">
            {items.map((item) => (
              <li
                key={item.media_item_id}
                data-testid={`collection-entry-${item.media_item_id}`}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-stage px-4 py-3 ring-1 ring-rule"
              >
                <span className="flex min-w-0 items-baseline gap-3">
                  <span className="tabular w-6 text-right text-ink-muted">{item.position + 1}</span>
                  <span className="min-w-0">
                    <span dir="auto" className="block truncate font-medium">
                      {item.title}
                    </span>
                    <span className="text-sm text-ink-muted">
                      {[item.year, item.custom?.emoji].filter(Boolean).join(" · ")}
                      {!item.available && (
                        <span className="ml-2 text-miss">{t("collections.itemUnavailable")}</span>
                      )}
                    </span>
                  </span>
                </span>
                <SaveButton
                  mediaItemId={item.media_item_id}
                  domain={detail.domain}
                  source="collection"
                />
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

type PageData = CollectionPageOut;

/** Лайк и избранное: оптимистично в кэше страницы, ответ сервера уточняет, ошибка — откат. */
function Reactions({ collectionId, me }: { collectionId: string; me: CollectionPageOut["me"] }) {
  const t = useTranslations();
  const toast = useToast();
  const client = useQueryClient();
  const viewer = useViewer();
  const key = collectionKeys.page(collectionId);
  const [pending, setPending] = useState<"like" | "favorite" | null>(null);

  if (viewer.kind !== "user" || !me) {
    return (
      <div className="flex flex-wrap gap-3">
        <Link
          href="/register"
          className={cn(buttonVariants({ variant: "secondary" }))}
          data-testid="collection-like"
          aria-pressed={false}
          role="button"
        >
          <Heart aria-hidden />
          {t("collections.like")}
        </Link>
        <Link
          href="/register"
          className={cn(buttonVariants({ variant: "secondary" }))}
          data-testid="collection-favorite"
          aria-pressed={false}
          role="button"
        >
          <Star aria-hidden />
          {t("collections.favorite")}
        </Link>
      </div>
    );
  }

  const patch = (fn: (p: PageData) => PageData) => {
    const before = client.getQueryData<PageData>(key);
    if (before) client.setQueryData<PageData>(key, fn(before));
    return before;
  };

  const toggleLike = async () => {
    const on = !me.liked;
    setPending("like");
    const before = patch((p) =>
      withLike(p, on, (p.blocks.collection.data?.likes_count ?? 0) + (on ? 1 : -1)),
    );
    try {
      const res = await setLike(collectionId, on);
      patch((p) => withLike(p, res.liked, res.likes_count));
    } catch (e) {
      if (before) client.setQueryData(key, before);
      toast(apiErrorText(t, e));
    } finally {
      setPending(null);
    }
  };

  const toggleFavorite = async () => {
    const on = !me.favorite;
    setPending("favorite");
    const before = patch((p) => withMe(p, { favorite: on }));
    try {
      const res = await setFavorite(collectionId, on);
      patch((p) => withMe(p, { favorite: res.favorite }));
    } catch (e) {
      if (before) client.setQueryData(key, before);
      toast(apiErrorText(t, e));
    } finally {
      setPending(null);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="secondary"
        aria-pressed={me.liked}
        disabled={me.is_author || pending === "like"}
        title={me.is_author ? t("collections.ownLike") : undefined}
        onClick={() => void toggleLike()}
        data-testid="collection-like"
        className={cn(me.liked && "text-miss")}
      >
        <Heart aria-hidden className={cn(me.liked && "fill-current")} />
        {me.liked ? t("collections.liked") : t("collections.like")}
      </Button>
      <Button
        variant="secondary"
        aria-pressed={me.favorite}
        disabled={pending === "favorite"}
        onClick={() => void toggleFavorite()}
        data-testid="collection-favorite"
        className={cn(me.favorite && "text-partial")}
      >
        <Star aria-hidden className={cn(me.favorite && "fill-current")} />
        {me.favorite ? t("collections.favorited") : t("collections.favorite")}
      </Button>
      {me.completed && (
        <span
          className="flex items-center gap-2 text-sm text-exact"
          data-testid="collection-completed"
        >
          <CheckCircle2 aria-hidden className="size-4" />
          {t("collections.completed")}
        </span>
      )}
      {me.is_author && (
        <Button asChild variant="quiet" size="sm">
          <Link href={`/collections/${collectionId}/edit`}>
            <Pencil aria-hidden />
            {t("collections.edit")}
          </Link>
        </Button>
      )}
    </div>
  );
}

function withMe(p: PageData, me: Partial<NonNullable<PageData["me"]>>): PageData {
  return p.me ? { ...p, me: { ...p.me, ...me } } : p;
}

function withLike(p: PageData, liked: boolean, count: number): PageData {
  const data = p.blocks.collection.data;
  const next = withMe(p, { liked });
  if (!data) return next;
  return {
    ...next,
    blocks: {
      ...next.blocks,
      collection: { ...next.blocks.collection, data: { ...data, likes_count: Math.max(0, count) } },
    },
  };
}

/** Режимы сборника и число раундов; «Играть» — старт через `/play/{mode}?collection=…&rounds=…`. */
function ModesBlock({ page, detail }: { page: CollectionPageOut; detail: CollectionDetailOut }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const viewer = useViewer();
  const block = page.blocks.modes;
  const modes = block.status === "ok" ? (block.data ?? []) : [];
  const preferred = modes.find((m) => m.mode_key === detail.default_mode_key && m.available);
  const [selected, setSelected] = useState<string | null>(null);
  const mode =
    modes.find((m) => m.mode_key === selected) ??
    preferred ??
    modes.find((m) => m.available) ??
    null;
  const [rounds, setRounds] = useState<RoundOption | null>(null);
  const options = mode?.round_options ?? [];
  const round =
    rounds !== null && options.includes(rounds)
      ? rounds
      : (options.find((o) => fits(o, mode)) ?? null);
  const guestBlocked = mode && !mode.allow_guest && viewer.kind !== "user";

  return (
    <section
      aria-labelledby="collection-modes-title"
      className="flex flex-col gap-5 rounded-[var(--radius-ticket)] bg-stage p-5 ring-1 ring-rule sm:p-7"
    >
      <h2 id="collection-modes-title" className="font-display text-xl font-bold">
        {t("collections.play.title")}
      </h2>
      {block.status !== "ok" ? (
        <p className="text-ink-muted" role="status">
          {t("collections.play.unavailable")}
        </p>
      ) : modes.length === 0 ? (
        <p className="text-ink-muted">{t("collections.play.noModes")}</p>
      ) : (
        <>
          <div
            role="group"
            aria-label={t("collections.play.mode")}
            className="grid gap-2 sm:grid-cols-2"
          >
            {modes.map((m) => (
              <button
                key={m.mode_key}
                type="button"
                aria-pressed={mode?.mode_key === m.mode_key}
                disabled={!m.available}
                onClick={() => {
                  setSelected(m.mode_key);
                  setRounds(null);
                }}
                data-testid={`collection-mode-${m.mode_key}`}
                className={cn(
                  "flex flex-col items-start gap-1 rounded-xl px-4 py-3 text-left ring-1 ring-rule disabled:opacity-45",
                  mode?.mode_key === m.mode_key
                    ? "bg-stage-raised ring-2 ring-[var(--accent)]"
                    : "hover:bg-stage-raised",
                )}
              >
                <span className="font-medium">{dt(m.title_key, m.mode_key)}</span>
                <span className="tabular text-sm text-ink-muted">
                  {t("collections.play.playable", { playable: m.playable, total: m.total })}
                </span>
              </button>
            ))}
          </div>
          {mode && (
            <div
              role="group"
              aria-label={t("collections.play.rounds")}
              className="flex flex-wrap gap-2"
            >
              {options.map((o) => (
                <button
                  key={String(o)}
                  type="button"
                  aria-pressed={round === o}
                  disabled={!fits(o, mode)}
                  onClick={() => setRounds(o)}
                  data-testid={`collection-rounds-${o}`}
                  className={cn(
                    "tabular min-w-14 rounded-full px-4 py-2 text-sm ring-1 ring-rule disabled:opacity-40",
                    round === o
                      ? "bg-[var(--accent)] font-semibold text-night ring-transparent"
                      : "text-ink",
                  )}
                >
                  {o === "all" ? t("collections.play.all", { count: mode.playable }) : o}
                </button>
              ))}
            </div>
          )}
          {guestBlocked ? (
            <Button asChild size="lg" className="self-start">
              <Link href="/login" data-testid="collection-play">
                {t("collections.play.loginToPlay")}
              </Link>
            </Button>
          ) : (
            <Button
              size="lg"
              className="self-start"
              disabled={!mode?.available || round === null}
              onClick={() =>
                router.push(
                  `/play/${encodeURIComponent(mode!.mode_key)}?collection=${encodeURIComponent(detail.collection_id)}&rounds=${round}`,
                )
              }
              data-testid="collection-play"
            >
              <Play aria-hidden />
              {t("collections.play.start")}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

/** Вариант раундов выбирается, если играбельных единиц хватает («все» — всегда). */
function fits(option: RoundOption, mode: CollectionModeCard | null): boolean {
  if (!mode) return false;
  return option === "all" || option <= mode.playable;
}
