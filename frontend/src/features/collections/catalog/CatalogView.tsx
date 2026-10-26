"use client";
/**
 * Каталог сборников `/collections` (D3, макет «Сборники»): поиск, чипы быстрых порядков
 * («Популярные», «Новые», «Кураторские»), остальные фильтры — за кнопкой «Фильтры». Фильтры,
 * сортировка и поиск живут в адресе (`q`, `domain`, `mode`, `difficulty`, `curated`, `sort`),
 * поиск — с паузой ввода. Без поиска и сортировки — витрины bff (рекомендуемые, популярные,
 * новые); с поиском или выбранной сортировкой — бесконечная лента collections по keyset-курсору.
 * Результат — строки, не карточки.
 */
import { useEffect, useId, useRef, useState, type ComponentProps, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { CatalogQuery, CollectionSort, Domain } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { Chip } from "@/shared/ui/chip";
import { inputClass } from "@/shared/ui/field";
import { cn } from "@/shared/lib/cn";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { HubHeader } from "@/features/hubs/HubHeader";
import { SearchField } from "@/features/hubs/SearchField";
import { collectionKeys, fetchCatalogPage, fetchShowcase } from "../api";
import { useCollectionModes } from "../modes";
import { CollectionCard } from "./CollectionCard";

export const SEARCH_DEBOUNCE_MS = 300;
/** Как у collections: длиннее запрос не нужен (поиск по названию, описанию, тегам). */
const MAX_QUERY = 100;

const SORTS: CollectionSort[] = ["popular", "new", "likes", "difficulty", "featured"];
/** Порядки с собственным чипом; остальные выбираются в панели фильтров. */
const QUICK_SORTS = ["popular", "new"] as const;
const SHOWCASE: ("featured" | "popular" | "new")[] = ["featured", "popular", "new"];
const DIFFICULTIES = ["easy", "medium", "hard"] as const;
const DOMAINS: Domain[] = ["film", "music"];

type Filters = CatalogQuery & { sort?: CollectionSort };

/** Фильтры из адреса: неизвестные значения отбрасываются (адрес — пользовательский ввод). */
export function filtersFrom(params: URLSearchParams): Filters {
  const pick = <T extends string>(v: string | null, allowed: readonly T[]) =>
    v && (allowed as readonly string[]).includes(v) ? (v as T) : undefined;
  const q = params.get("q")?.trim().slice(0, MAX_QUERY) || undefined;
  const mode = params.get("mode");
  return {
    q,
    domain: pick(params.get("domain"), DOMAINS),
    sort: pick(params.get("sort"), SORTS),
    difficulty: pick(params.get("difficulty"), DIFFICULTIES),
    curated: params.get("curated") === "true" ? true : undefined,
    mode: mode && /^[a-z0-9_.]{1,100}$/.test(mode) ? mode : undefined,
  };
}

function toSearch(f: Filters): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f))
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Сколько фильтров из панели включено (чипы быстрых порядков и «Кураторские» не считаются). */
export function panelFiltersCount(f: Filters): number {
  const slowSort = f.sort && !(QUICK_SORTS as readonly string[]).includes(f.sort);
  return [f.domain, f.difficulty, f.mode, slowSort].filter(Boolean).length;
}

export function CatalogView() {
  const t = useTranslations();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const filters = filtersFrom(new URLSearchParams(params.toString()));
  const viewer = useViewer();
  const listMode = Boolean(filters.q || filters.sort);

  const update = (patch: Partial<Filters>) =>
    router.replace(`${pathname}${toSearch({ ...filters, ...patch })}`, { scroll: false });

  // Поиск пишет адрес через history API: router.replace по паузе ввода отменял бы
  // незавершённый router.push карточки (клик сразу после ввода). Next подхватывает
  // replaceState в useSearchParams, навигации при этом не отменяются.
  const search = (q: string | undefined) =>
    window.history.replaceState(
      null,
      "",
      `${pathname}${toSearch({ ...filters, q: q || undefined })}`,
    );

  return (
    <div className="flex flex-col gap-4 pt-4">
      <HubHeader
        title={t("collections.title")}
        aside={
          viewer.kind === "user" ? (
            <Link
              href="/me/collections"
              className="hover:text-ink"
              data-testid="catalog-my-collections"
            >
              {t("nav.myCollections")}
            </Link>
          ) : null
        }
      />

      <SearchBox value={filters.q ?? ""} onSearch={search} />
      <FilterBar filters={filters} onChange={update} />

      {listMode ? (
        <ResultsList filters={filters} />
      ) : (
        <Showcase filters={filters} onAll={(sort) => update({ sort })} />
      )}

      {viewer.kind === "user" && (
        <div className="flex justify-end pt-1.5">
          <Button asChild>
            <Link href="/collections/new" data-testid="catalog-create">
              {t("collections.createOwn")}
            </Link>
          </Button>
        </div>
      )}
    </div>
  );
}

function SearchBox({ value, onSearch }: { value: string; onSearch: (q: string) => void }) {
  const t = useTranslations();
  const [text, setText] = useState(value);
  const last = useRef(value);
  const onSearchRef = useRef(onSearch);
  useEffect(() => {
    onSearchRef.current = onSearch;
  });
  useEffect(() => {
    const q = text.trim().slice(0, MAX_QUERY);
    if (q === last.current) return;
    const timer = setTimeout(() => {
      last.current = q;
      onSearchRef.current(q);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);
  return (
    <SearchField
      aria-label={t("collections.search")}
      value={text}
      maxLength={MAX_QUERY}
      onChange={(e) => setText(e.target.value)}
      placeholder={t("collections.searchPlaceholder")}
      data-testid="collection-search"
    />
  );
}

function FilterBar({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (p: Partial<Filters>) => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const panelId = useId();
  const sortId = useId();
  const modeId = useId();
  const [open, setOpen] = useState(false);
  const modes = useCollectionModes(filters.domain, filters.mode);
  const count = panelFiltersCount(filters);
  const toggleSort = (sort: CollectionSort) =>
    onChange({ sort: filters.sort === sort ? undefined : sort });
  return (
    <div className="flex flex-col gap-3" data-testid="collection-filters">
      <div className="flex flex-wrap items-center gap-2">
        {QUICK_SORTS.map((s) => (
          <Chip key={s} selected={filters.sort === s} onClick={() => toggleSort(s)}>
            {t(`collections.sort.${s}`)}
          </Chip>
        ))}
        <Chip
          selected={filters.curated === true}
          onClick={() => onChange({ curated: filters.curated ? undefined : true })}
        >
          {t("collections.filters.curatedChip")}
        </Chip>
        <Chip
          className="ml-auto"
          selected={count > 0}
          aria-pressed={undefined}
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((v) => !v)}
          data-testid="collection-filters-toggle"
        >
          {count > 0
            ? t("collections.filters.openCount", { count })
            : t("collections.filters.open")}
        </Chip>
      </div>
      {open && (
        <div
          id={panelId}
          className="flex animate-rise flex-col gap-4 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage p-4"
        >
          <FilterGroup label={t("collections.filters.domain")}>
            {[undefined, ...DOMAINS].map((d) => (
              <Chip
                key={d ?? "all"}
                selected={filters.domain === d}
                onClick={() => onChange({ domain: d, mode: undefined })}
              >
                {d ? t(`domain.${d}`) : t("collections.filters.all")}
              </Chip>
            ))}
          </FilterGroup>
          <FilterGroup label={t("collections.filters.difficulty")}>
            {[undefined, ...DIFFICULTIES].map((d) => (
              <Chip
                key={d ?? "any"}
                selected={filters.difficulty === d}
                onClick={() => onChange({ difficulty: d })}
              >
                {d ? t(`collections.difficulty.${d}`) : t("collections.filters.any")}
              </Chip>
            ))}
          </FilterGroup>
          <div className="flex flex-wrap gap-3">
            <SelectField id={sortId} label={t("collections.filters.sort")}>
              <select
                id={sortId}
                value={filters.sort ?? ""}
                onChange={(e) =>
                  onChange({ sort: (e.target.value || undefined) as CollectionSort | undefined })
                }
                className={selectClass}
                data-testid="collection-sort"
              >
                <option value="">{t("collections.sort.showcase")}</option>
                {SORTS.map((s) => (
                  <option key={s} value={s}>
                    {t(`collections.sort.${s}`)}
                  </option>
                ))}
              </select>
            </SelectField>
            <SelectField id={modeId} label={t("collections.filters.mode")}>
              <select
                id={modeId}
                value={filters.mode ?? ""}
                onChange={(e) => onChange({ mode: e.target.value || undefined })}
                className={selectClass}
              >
                <option value="">{t("collections.filters.any")}</option>
                {modes.map((m) => (
                  <option key={m.key} value={m.key}>
                    {m.titleKey ? dt(m.titleKey, m.key) : m.key}
                  </option>
                ))}
              </select>
            </SelectField>
          </div>
        </div>
      )}
    </div>
  );
}

const selectClass = cn(inputClass, "h-11 min-w-44 px-3 text-sm");

function FilterGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-col gap-2">
      <span className="text-xs text-ink-muted">{label}</span>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function SelectField({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-xs text-ink-muted">
        {label}
      </label>
      {children}
    </div>
  );
}

function CardList({ cards }: { cards: ComponentProps<typeof CollectionCard>["card"][] }) {
  return (
    <ul className="flex flex-col gap-2.5">
      {cards.map((card) => (
        <CollectionCard key={card.collection_id} card={card} />
      ))}
    </ul>
  );
}

function Showcase({ filters, onAll }: { filters: Filters; onAll: (sort: CollectionSort) => void }) {
  const t = useTranslations();
  const query = { ...filters, q: undefined, sort: undefined };
  const page = useQuery({
    queryKey: collectionKeys.showcase(query),
    queryFn: () => fetchShowcase(query),
  });
  if (page.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (page.isError) return <p className="text-ink-muted">{t("collections.unavailable")}</p>;
  const blocks = SHOWCASE.map((name) => ({ name, block: page.data.blocks[name] })).filter(
    (b) => b.block && (b.block.status !== "ok" || (b.block.data?.items.length ?? 0) > 0),
  );
  if (!blocks.length) return <p className="text-ink-muted">{t("collections.empty")}</p>;
  return (
    <>
      {blocks.map(({ name, block }) => (
        <section
          key={name}
          aria-labelledby={`showcase-${name}`}
          className="flex flex-col gap-2.5"
          data-testid={`collections-block-${name}`}
        >
          <div className="flex items-baseline justify-between gap-4 px-0.5 pt-2">
            <h2 id={`showcase-${name}`} className="text-sm font-medium text-ink-muted">
              {t(`collections.blocks.${name}`)}
            </h2>
            {block?.status === "ok" && block.data?.next_cursor && (
              <Button variant="quiet" size="sm" onClick={() => onAll(name)}>
                {t("collections.showAll")}
              </Button>
            )}
          </div>
          {block?.status !== "ok" ? (
            <p className="text-ink-muted">{t("collections.unavailable")}</p>
          ) : (
            <CardList cards={block.data?.items ?? []} />
          )}
        </section>
      ))}
    </>
  );
}

function ResultsList({ filters }: { filters: Filters }) {
  const t = useTranslations();
  const list = useInfiniteQuery({
    queryKey: collectionKeys.list(filters),
    queryFn: ({ pageParam, signal }) => fetchCatalogPage(filters, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? null,
  });
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list;

  useEffect(() => {
    const el = sentinel.current;
    if (!el || !hasNextPage || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !isFetchingNextPage) void fetchNextPage();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (list.isPending)
    return (
      <p className="text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  if (list.isError) return <p className="text-ink-muted">{t("collections.unavailable")}</p>;
  const cards = list.data.pages.flatMap((p) => p.items);
  return (
    <section
      aria-label={t("collections.results")}
      className="flex flex-col gap-2.5"
      data-testid="collections-results"
    >
      {cards.length === 0 ? (
        <p className="text-ink-muted">{t("collections.nothingFound")}</p>
      ) : (
        <CardList cards={cards} />
      )}
      <div ref={sentinel} />
      {hasNextPage && (
        <Button
          variant="secondary"
          className="self-start"
          disabled={isFetchingNextPage}
          onClick={() => void fetchNextPage()}
          data-testid="collections-more"
        >
          {isFetchingNextPage ? t("common.loading") : t("collections.more")}
        </Button>
      )}
    </section>
  );
}
