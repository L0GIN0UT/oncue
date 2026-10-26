"use client";
/**
 * Карточка сборника в каталоге (макет «Сборники»): обложка-пресет с чипами домена и числа единиц,
 * название (UGC, `dir="auto"`), автор, сложность, лайки, прохождения и «Играть». Вся карточка —
 * ссылка на сборник (он открывается модалкой).
 */
import { useLocale, useTranslations } from "next-intl";
import { Heart } from "lucide-react";
import type { CollectionCard as Card, CollectionCardOut } from "@/shared/api/types";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { useIcons } from "@/shared/ui/theme-provider";
import { Tile } from "@/shared/ui/tile";
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

const DIFFICULTY_TONE = {
  easy: "text-exact",
  medium: "text-music",
  hard: "text-miss",
} as const;

/** Число для карточки: 1 204 → «1,2к», меньше тысячи — как есть. */
function compact(n: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function CollectionCard({ card }: Props) {
  const t = useTranslations();
  const locale = useLocale();
  const authorName = useAuthorName();
  const icons = useIcons();
  const DomainIcon = icons[card.domain];
  return (
    <li data-domain={card.domain}>
      <Tile
        href={`/collections/${card.collection_id}`}
        data-testid={`collection-card-${card.collection_id}`}
        className="h-full gap-0 p-0"
      >
        <div className="relative">
          <Cover preset={card.cover?.preset} className="aspect-[16/10] rounded-none ring-0" />
          <span className="absolute top-2.5 left-2.5 inline-flex items-center gap-1.5 rounded-full bg-night/85 px-[9px] py-[3px] text-xs font-semibold text-music">
            <DomainIcon aria-hidden className="size-3.5" />
            {t(`domain.${card.domain}`)}
          </span>
          <span className="tabular absolute right-2.5 bottom-2.5 rounded-full bg-night/85 px-[9px] py-[3px] text-xs font-semibold">
            {card.items_count}
          </span>
        </div>
        <div className="flex flex-1 flex-col gap-1.5 px-4 pt-3 pb-3.5">
          <span dir="auto" className="truncate text-base font-semibold">
            {card.title}
          </span>
          <span dir="auto" className="truncate text-xs text-ink-muted">
            {t("collections.row.by", { name: authorName(card.author) })}
          </span>
          <div className="flex items-center justify-between gap-2 text-xs text-ink-muted">
            <span
              className={cn(
                "font-semibold",
                card.difficulty ? DIFFICULTY_TONE[card.difficulty] : "text-ink-muted",
              )}
            >
              {card.difficulty
                ? t(`collections.difficulty.${card.difficulty}`).toLowerCase()
                : null}
            </span>
            <span className="tabular inline-flex items-center gap-1">
              <Heart aria-hidden className="size-3.5 text-miss" />
              <span className="sr-only">{t("collections.stats.likes")}</span>
              {compact(card.likes_count, locale)}
            </span>
            <span className="tabular">
              {t("collections.row.plays", { count: compact(card.plays_count, locale) })}
            </span>
          </div>
          <span className="mt-1.5 grid h-8 place-items-center rounded-full border-[1.5px] border-rule bg-stage-raised text-[13px] font-semibold">
            {t("collections.play.start")}
          </span>
        </div>
      </Tile>
    </li>
  );
}
