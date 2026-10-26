"use client";
/**
 * Строка сборника в каталоге (макет «Сборники», 68 px): обложка-пресет, название (UGC,
 * `dir="auto"`), «N треков · сложность · автор», лайки и стрелка. Вся строка — ссылка.
 */
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Heart } from "lucide-react";
import type { CollectionCard as Card, CollectionCardOut } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useIcons } from "@/shared/ui/theme-provider";
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
  const authorName = useAuthorName();
  const Next = useIcons().next;
  const meta = [
    t(`collections.row.items.${card.domain}`, { count: card.items_count }),
    card.difficulty ? t(`collections.difficulty.${card.difficulty}`) : null,
    card.kind === "curated" ? t("collections.row.curated") : authorName(card.author),
  ].filter(Boolean);
  return (
    <li data-domain={card.domain}>
      <Link
        href={`/collections/${card.collection_id}`}
        data-testid={`collection-card-${card.collection_id}`}
        className="flex min-h-[68px] w-full items-center gap-3.5 rounded-[var(--radius-control)] border-[1.5px] border-rule px-[18px] py-2 transition-colors hover:border-rule-strong hover:bg-stage"
      >
        <Cover preset={card.cover?.preset} size="sm" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span dir="auto" className="truncate font-semibold">
            {card.title}
          </span>
          <span dir="auto" className="truncate text-[13px] text-ink-muted">
            {meta.join(" · ")}
          </span>
        </span>
        <span className="tabular flex items-center gap-1 text-[13px] text-ink-muted">
          <Heart aria-hidden className="size-4" />
          <span className="sr-only">{t("collections.stats.likes")}</span>
          {card.likes_count}
        </span>
        <Next aria-hidden size={18} className="shrink-0 text-ink-muted" />
      </Link>
    </li>
  );
}
