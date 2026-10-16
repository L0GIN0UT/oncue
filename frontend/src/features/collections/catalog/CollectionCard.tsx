"use client";
/** Карточка сборника в каталоге: обложка-пресет, название (UGC, `dir="auto"`), автор, счётчики. */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Heart, Layers, Play } from "lucide-react";
import type { CollectionCard as Card, CollectionCardOut } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useCollectionsMeta } from "../api";
import { Cover } from "../Cover";

type Props = { card: Card | CollectionCardOut };

/** Подпись автора: ник пользователя, «Guessverse» у кураторских, «автор удалён». */
export function useAuthorName(): (author: Card["author"]) => string {
  const t = useTranslations();
  return (author) =>
    author.kind === "curator"
      ? t("collections.author.curator")
      : author.kind === "deleted"
        ? t("collections.author.deleted")
        : (author.nickname ?? t("collections.author.unknown"));
}

/** Название тега по словарю `GET /v1/meta` (`title_key`); неизвестный тег — его ключ. */
export function useTagTitle(): (key: string) => string {
  const dt = useDynamicT();
  const meta = useCollectionsMeta();
  return (key) => {
    const tag = meta.data?.tags?.find((x) => x.key === key);
    return tag ? dt(tag.title_key, key) : key;
  };
}

export function CollectionCard({ card }: Props) {
  const t = useTranslations();
  const tagTitle = useTagTitle();
  const authorName = useAuthorName();
  return (
    <li data-domain={card.domain}>
      <Link
        href={`/collections/${card.collection_id}`}
        data-testid={`collection-card-${card.collection_id}`}
        className="group flex h-full flex-col gap-3 rounded-[var(--radius-ticket)] bg-stage p-3 ring-1 ring-rule transition-colors hover:bg-stage-raised"
      >
        <Cover preset={card.cover?.preset} />
        <div className="flex flex-1 flex-col gap-1 px-1">
          <p className="text-xs text-[var(--accent)]">
            {t(`domain.${card.domain}`)}
            {card.kind === "curated" ? ` · ${t("collections.curated")}` : ""}
            {card.difficulty ? ` · ${t(`collections.difficulty.${card.difficulty}`)}` : ""}
          </p>
          <h3 dir="auto" className="line-clamp-2 font-display text-lg leading-snug font-bold">
            {card.title}
          </h3>
          <p dir="auto" className="truncate text-sm text-ink-muted">
            {authorName(card.author)}
          </p>
          {card.tags.length > 0 && (
            <p className="truncate text-xs text-ink-faint">{card.tags.map(tagTitle).join(" · ")}</p>
          )}
          <p className="tabular mt-auto flex gap-4 pt-2 text-sm text-ink-muted">
            <span className="flex items-center gap-1">
              <Layers aria-hidden className="size-4" />
              <span className="sr-only">{t("collections.stats.items")}</span>
              {card.items_count}
            </span>
            <span className="flex items-center gap-1">
              <Heart aria-hidden className="size-4" />
              <span className="sr-only">{t("collections.stats.likes")}</span>
              {card.likes_count}
            </span>
            <span className="flex items-center gap-1">
              <Play aria-hidden className="size-4" />
              <span className="sr-only">{t("collections.stats.plays")}</span>
              {card.plays_count}
            </span>
          </p>
        </div>
      </Link>
    </li>
  );
}
