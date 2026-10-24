"use client";
/**
 * Каталог сборников `/collections` (D3). Фильтры, сортировка и поиск живут в адресе (`q`,
 * `domain`, `mode`, `difficulty`, `curated`, `sort`), поиск — с паузой ввода. Без поиска и
 * сортировки — витрины bff (рекомендуемые, популярные, новые); с поиском или выбранной
 * сортировкой — бесконечная лента collections по keyset-курсору.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Plus, Search } from "lucide-react";
import type { CatalogQuery, CollectionSort, Domain } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { collectionKeys, fetchCatalogPage, fetchShowcase } from "../api";
import { useCollectionModes } from "../modes";
import { CollectionCard } from "./CollectionCard";

export const SEARCH_DEBOUNCE_MS = 300;
/** Как у collections: длиннее запрос не нужен (поиск по названию, описанию, тегам). */
const MAX_QUERY = 100;

const SORTS: CollectionSort[] = ["popular", "new", "likes", "difficulty", "featured"];
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
    <div className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="font-display text-3xl font-bold sm:text-4xl">{t("collections.title")}</h1>
          <p className="max-w-[60ch] text-ink-muted">{t("collections.lead")}</p>
        </div>
        {viewer.kind === "user" && (
          <Button asChild>
            <Link href="/collections/new">
              <Plus aria-hidden />
              {t("collections.create")}
            </Link>
          </Button>
        )}
      </header>

      <SearchBox value={filters.q ?? ""} onSearch={search} />
      <FilterBar filters={filters} onChange={update} />

      {listMode ? (
        <ResultsList filters={filters} />
      ) : (
        <Showcase filters={filters} onAll={(sort) => update({ sort })} />
      )}
    </div>
  );
}

function SearchBox({ value, onSearch }: { value: string; onSearch: (q: string) => void }) {
  const t = useTranslations();
  const id = useId();
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
    <div className="relative">
      <label htmlFor={id} className="sr-only">
        {t("collections.search")}
      </label>
      <Search
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-4 size-5 -translate-y-1/2 text-ink-muted"
      />
      <input
        id={id}
        type="search"
        value={text}
        maxLength={MAX_QUERY}
        onChange={(e) => setText(e.target.value)}
        placeholder={t("collections.searchPlaceholder")}
        data-testid="collection-search"
        className="h-12 w-full rounded-[var(--radius-control)] bg-night pr-4 pl-12 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-music"
      />
    </div>
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
  const modes = useCollectionModes(filters.domain, filters.mode);
  const sortId = useId();
  const difficultyId = useId();
  const modeId = useId();
  return (
    <div className="flex flex-wrap items-end gap-4" data-testid="collection-filters">
      <div
        role="group"
        aria-label={t("collections.filters.domain")}
        className="flex gap-1 rounded-[var(--radius-control)] bg-stage p-1 ring-1 ring-rule"
      >
        {[undefined, ...DOMAINS].map((d) => (
          <button
            key={d ?? "all"}
            type="button"
            aria-pressed={filters.domain === d}
            onClick={() => onChange({ domain: d, mode: undefined })}
            className={cn(
              "rounded-lg px-3 py-1.5 text-sm",
              filters.domain === d
                ? "bg-stage-raised text-ink ring-1 ring-rule"
                : "text-ink-muted hover:text-ink",
            )}
          >
            {d ? t(`domain.${d}`) : t("collections.filters.all")}
          </button>
        ))}
      </div>
      <Select id={sortId} label={t("collections.filters.sort")}>
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
      </Select>
      <Select id={difficultyId} label={t("collections.filters.difficulty")}>
        <select
          id={difficultyId}
          value={filters.difficulty ?? ""}
          onChange={(e) =>
            onChange({ difficulty: (e.target.value || undefined) as Filters["difficulty"] })
          }
          className={selectClass}
        >
          <option value="">{t("collections.filters.any")}</option>
          {DIFFICULTIES.map((d) => (
            <option key={d} value={d}>
              {t(`collections.difficulty.${d}`)}
            </option>
          ))}
        </select>
      </Select>
      <Select id={modeId} label={t("collections.filters.mode")}>
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
      </Select>
      <label className="flex h-10 items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={filters.curated === true}
          onChange={(e) => onChange({ curated: e.target.checked ? true : undefined })}
          className="size-4 accent-[var(--color-music)]"
        />
        {t("collections.filters.curated")}
      </label>
    </div>
  );
}

const selectClass =
  "h-10 rounded-[var(--radius-control)] bg-night px-3 text-sm text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-music";

function Select({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-ink-muted">
        {label}
      </label>
      {children}
    </div>
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
          className="flex flex-col gap-4"
          data-testid={`collections-block-${name}`}
        >
          <div className="flex items-baseline justify-between gap-4">
            <h2 id={`showcase-${name}`} className="font-display text-xl font-bold">
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
            <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {block.data?.items.map((card) => (
                <CollectionCard key={card.collection_id} card={card} />
              ))}
            </ul>
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
      className="flex flex-col gap-4"
      data-testid="collections-results"
    >
      {cards.length === 0 ? (
        <p className="text-ink-muted">{t("collections.nothingFound")}</p>
      ) : (
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {cards.map((card) => (
            <CollectionCard key={card.collection_id} card={card} />
          ))}
        </ul>
      )}
      <div ref={sentinel} />
      {hasNextPage && (
        <Button
          variant="secondary"
          className="self-center"
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
