"use client";
/**
 * Карточка сборника `/collections/{id}` модалкой по макету v3 (CollectionDetail): слева обложка с
 * названием, справа числа, теги, описание, лайк и избранное, формат (режим game «N из M» и число
 * раундов из `round_options`) с единственной янтарной кнопкой «Играть», игра компанией, комментарии,
 * состав — только если сервер его прислал (`items`) — и жалоба. Лайк и избранное оптимистичны (откат
 * при ошибке); гостю и анониму — ссылки на регистрацию. Каталог остаётся под модалкой; закрытие
 * возвращает к каталогу с теми же фильтрами.
 */
import { useState } from "react";
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
import { Chip } from "@/shared/ui/chip";
import { ListRow } from "@/shared/ui/list-row";
import { Modal } from "@/shared/ui/modal";
import { SectionCap } from "@/shared/ui/section-cap";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useIcons } from "@/shared/ui/theme-provider";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { CommentsSection } from "@/features/comments/CommentsSection";
import { ReportButton } from "@/features/reports/ReportDialog";
import { SaveButton } from "@/features/library/SaveButton";
import { PlayWithFriends } from "@/features/match/PlayWithFriends";
import { collectionKeys, fetchCollectionPage, setFavorite, setLike } from "../api";
import { Cover } from "../Cover";
import { apiErrorText } from "../errors";
import { useAuthorName, useTagTitle } from "./CollectionCard";

/** Куда вернуться после закрытия: адрес каталога с фильтрами, который запомнил каталог. */
export const CATALOG_RETURN_KEY = "gv:catalog-return";

function catalogUrl(): string {
  try {
    const saved = sessionStorage.getItem(CATALOG_RETURN_KEY);
    if (saved?.startsWith("/collections")) return saved;
  } catch {
    /* хранилище недоступно (приватный режим) */
  }
  return "/collections";
}

/** Модалка сборника поверх каталога: Escape и клик по фону возвращают к каталогу. */
export function CollectionModal({ collectionId }: { collectionId: string }) {
  const t = useTranslations();
  const router = useRouter();
  const Close = useIcons().close;
  const close = () => router.push(catalogUrl());
  return (
    <Modal
      open
      onClose={close}
      aria-label={t("collections.pageTitle")}
      data-testid="collection-modal"
      className="flex max-h-[min(92vh,700px)] max-w-[980px] flex-col"
    >
      <button
        type="button"
        onClick={close}
        aria-label={t("common.close")}
        data-testid="collection-close"
        className="absolute top-4 right-4 z-10 grid size-[34px] place-items-center rounded-full border-[1.5px] border-rule bg-stage text-ink-muted hover:border-rule-strong hover:text-ink"
      >
        <Close aria-hidden className="size-4" />
      </button>
      <CollectionView collectionId={collectionId} />
    </Modal>
  );
}

export function CollectionView({ collectionId }: { collectionId: string }) {
  const t = useTranslations();
  const page = useQuery({
    queryKey: collectionKeys.page(collectionId),
    queryFn: () => fetchCollectionPage(collectionId),
  });

  if (page.isPending)
    return (
      <p className="p-10 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (page.isError) {
    const missing = isApiError(page.error) && page.error.status === 404;
    return (
      <div className="flex flex-col items-start gap-4 p-8">
        <h1 className="font-display text-[28px] leading-tight font-extrabold tracking-[-0.03em]">
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
    return <p className="p-10 text-ink-muted">{t("collections.unavailable")}</p>;
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
  const unit = t(`collections.row.items.${detail.domain}`, { count: detail.items_count });
  return (
    <article
      data-domain={detail.domain}
      className="grid min-h-0 flex-1 md:grid-cols-[340px_minmax(0,1fr)]"
    >
      <div className="relative min-h-44 md:min-h-0">
        <Cover
          preset={detail.cover?.preset}
          className="absolute inset-0 size-full rounded-none text-8xl ring-0"
        />
        <div className="absolute inset-x-0 bottom-0 flex flex-col gap-1.5 bg-gradient-to-t from-night/90 to-transparent px-[22px] pt-16 pb-[22px]">
          <h1
            dir="auto"
            className="font-display text-[26px] leading-[1.1] font-extrabold tracking-[-0.02em] text-balance"
          >
            {detail.title}
          </h1>
          <p className="text-[13px] text-ink-soft">
            {detail.kind === "curated" ? t("collections.curated") : authorName(detail.author)}
            {" · "}
            {t(`domain.${detail.domain}`)}
            {detail.featured ? ` · ${t("collections.featured")}` : ""}
          </p>
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-3.5 overflow-y-auto p-[22px] md:px-[26px]">
        <p
          className="flex flex-wrap items-center gap-x-[18px] gap-y-1 pr-11 text-[13px] text-ink-muted"
          data-testid="collection-stats"
        >
          <span>
            <b className="tabular text-ink" data-testid="collection-items-count">
              {detail.items_count}
            </b>{" "}
            <span className="sr-only">{t("collections.stats.items")}: </span>
            {unit.replace(/^\d+\s*/, "")}
          </span>
          <span>
            {detail.difficulty
              ? t(`collections.difficulty.${detail.difficulty}`).toLowerCase()
              : t("collections.difficulty.unknown")}
          </span>
          <span className="inline-flex items-center gap-1">
            <Heart aria-hidden className="size-3.5 text-miss" />
            <span className="sr-only">{t("collections.stats.likes")}: </span>
            <b className="tabular text-ink" data-testid="collection-likes-count">
              {detail.likes_count}
            </b>
          </span>
          <span>
            {t("collections.stats.played")} <b className="tabular text-ink">{detail.plays_count}</b>
          </span>
        </p>
        <p className="text-[13px] text-ink-muted">
          {t("collections.by")}{" "}
          <span dir="auto" className="text-ink" data-testid="collection-author">
            {authorName(detail.author)}
          </span>
        </p>
        {detail.tags.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label={t("collections.tagsLabel")}>
            {detail.tags.map((k) => (
              <li
                key={k}
                className="inline-flex h-6 items-center rounded-full bg-stage-raised px-[9px] text-xs font-semibold text-ink-soft"
              >
                {tagTitle(k)}
              </li>
            ))}
          </ul>
        )}
        {detail.description && (
          <p dir="auto" className="text-sm whitespace-pre-line text-ink-muted">
            {detail.description}
          </p>
        )}
        {detail.status === "hidden" && (
          <p
            className="rounded-[var(--radius-control)] border-[1.5px] border-miss-line bg-miss-fill px-4 py-3 text-sm"
            role="note"
          >
            {t("collections.hiddenByModerator")}
          </p>
        )}
        <Reactions collectionId={detail.collection_id} me={me} />

        <ModesBlock page={page} detail={detail} />
        <PlayWithFriends collectionId={detail.collection_id} domain={detail.domain} />
        <CommentsSection collectionId={detail.collection_id} block={page.blocks.comments} />
        {me && !me.is_author && (
          <div className="flex">
            <ReportButton
              kind="collection"
              targetId={detail.collection_id}
              label={t("social.report.collection")}
              testId="collection-report"
            />
          </div>
        )}

        {items && (
          <section
            aria-labelledby="collection-items-title"
            className="flex flex-col gap-2"
            data-testid="collection-items"
          >
            <h2 id="collection-items-title">
              <SectionCap>{t("collections.items")}</SectionCap>
            </h2>
            <ol className="flex flex-col gap-1.5">
              {items.map((item) => (
                <li key={item.media_item_id} data-testid={`collection-entry-${item.media_item_id}`}>
                  <ListRow
                    lead={item.position + 1}
                    trailing={
                      <SaveButton
                        mediaItemId={item.media_item_id}
                        domain={detail.domain}
                        source="collection"
                      />
                    }
                    className="min-h-12"
                  >
                    <span dir="auto" className="font-semibold">
                      {item.title}
                    </span>
                    <span className="text-xs text-ink-muted">
                      {" · "}
                      {[item.year, item.custom?.emoji].filter(Boolean).join(" · ")}
                      {!item.available && (
                        <span className="ml-2 text-ink-soft">
                          {t("collections.itemUnavailable")}
                        </span>
                      )}
                    </span>
                  </ListRow>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
    </article>
  );
}

type PageData = CollectionPageOut;

/** Лайк и избранное: оптимистично в кэше страницы, ответ сервера уточняет, ошибка — откат. */
function Reactions({ collectionId, me }: { collectionId: string; me: CollectionPageOut["me"] }) {
  const t = useTranslations();
  const toast = useToast();
  const client = useQueryClient();
  const key = collectionKeys.page(collectionId);
  const [pending, setPending] = useState<"like" | "favorite" | null>(null);

  // `me` bff отдаёт только пользователю (гостю и анониму — null, D7).
  if (!me) {
    return (
      <div className="flex flex-wrap gap-2">
        <Link
          href="/register"
          className={cn(buttonVariants({ variant: "secondary", size: "sm" }))}
          data-testid="collection-like"
          aria-pressed={false}
          role="button"
        >
          <Heart aria-hidden />
          {t("collections.like")}
        </Link>
        <Link
          href="/register"
          className={cn(buttonVariants({ variant: "secondary", size: "sm" }))}
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
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        size="sm"
        aria-pressed={me.liked}
        disabled={me.is_author || pending === "like"}
        title={me.is_author ? t("collections.ownLike") : undefined}
        onClick={() => void toggleLike()}
        data-testid="collection-like"
        className={cn(me.liked && "border-music text-music")}
      >
        <Heart aria-hidden className={cn(me.liked && "fill-current")} />
        {me.liked ? t("collections.liked") : t("collections.like")}
      </Button>
      <Button
        variant="secondary"
        size="sm"
        aria-pressed={me.favorite}
        disabled={pending === "favorite"}
        onClick={() => void toggleFavorite()}
        data-testid="collection-favorite"
        className={cn(me.favorite && "border-music text-music")}
      >
        <Star aria-hidden className={cn(me.favorite && "fill-current")} />
        {me.favorite ? t("collections.favorited") : t("collections.favorite")}
      </Button>
      {me.completed && (
        <span
          className="flex items-center gap-2 text-sm text-ink-soft"
          data-testid="collection-completed"
        >
          <CheckCircle2 aria-hidden className="size-4 text-exact" />
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

/**
 * Формат игры: режим (если их несколько), число раундов и «Играть» — старт через
 * `/play/{mode}?collection=…&rounds=…`. Названия размеров («разминка», «партия») — из словаря.
 */
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
      className="mt-auto flex flex-col gap-2.5 pt-2"
    >
      <h2 id="collection-modes-title">
        <SectionCap>{t("collections.play.title")}</SectionCap>
      </h2>
      {block.status !== "ok" ? (
        <p className="text-sm text-ink-muted" role="status">
          {t("collections.play.unavailable")}
        </p>
      ) : modes.length === 0 ? (
        <p className="text-sm text-ink-muted">{t("collections.play.noModes")}</p>
      ) : (
        <>
          <div
            role="group"
            aria-label={t("collections.play.mode")}
            className="flex flex-col gap-1.5"
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
                  "flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border-[1.5px] bg-stage-raised px-3 py-1.5 text-left text-sm transition-colors disabled:opacity-45",
                  mode?.mode_key === m.mode_key
                    ? "border-music"
                    : "border-transparent hover:border-rule-strong",
                )}
              >
                <span className="font-semibold">{dt(m.title_key, m.mode_key)}</span>
                <span className="tabular text-xs text-ink-muted">
                  {t("collections.play.playable", { playable: m.playable, total: m.total })}
                </span>
              </button>
            ))}
          </div>
          {mode && (
            <div
              role="group"
              aria-label={t("collections.play.rounds")}
              className="flex flex-wrap items-center gap-2"
            >
              {options.map((o) => {
                const name = o === "all" ? "" : dt(`collections.play.size.${o}`, "");
                return (
                  <Chip
                    key={String(o)}
                    selected={round === o}
                    disabled={!fits(o, mode)}
                    onClick={() => setRounds(o)}
                    data-testid={`collection-rounds-${o}`}
                    className="tabular h-10 rounded-[14px] px-4 text-sm disabled:opacity-40"
                  >
                    {o === "all"
                      ? t("collections.play.all", { count: mode.playable })
                      : name
                        ? `${o} · ${name}`
                        : o}
                  </Chip>
                );
              })}
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
