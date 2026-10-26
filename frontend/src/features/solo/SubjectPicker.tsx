"use client";
/**
 * Выбор персон перед стартом марафона (`start.subject` режима, D14): поиск по
 * `GET /v1/modes/{key}/subjects?q=` с паузой ввода 200 мс, от одной до `max` персон, затем
 * «Начать» — `POST /v1/sessions {mode_key, subject_ids}`. Пустой запрос — персоны с самым
 * большим числом единиц.
 */
import { useEffect, useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import type { ModeOut, SubjectOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useIcons } from "@/shared/ui/theme-provider";
import { HubHeader } from "@/features/hubs/HubHeader";
import { SearchField } from "@/features/hubs/SearchField";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { cn } from "@/shared/lib/cn";
import { withSession } from "@/features/auth/session";

export const SUBJECT_SEARCH_DEBOUNCE_MS = 200;
const SUBJECTS_LIMIT = 20;
const QUERY_MAX = 100;

/** Значение, которое «догоняет» `value` после паузы `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export async function searchSubjects(
  modeKey: string,
  q: string,
  signal?: AbortSignal,
): Promise<SubjectOut[]> {
  const query = q.trim().slice(0, QUERY_MAX);
  return withSession(async () =>
    unwrap<SubjectOut[]>(
      await api().game.GET("/v1/modes/{mode_key}/subjects", {
        params: {
          path: { mode_key: modeKey },
          query: { limit: SUBJECTS_LIMIT, ...(query ? { q: query } : {}) },
        },
        signal,
      }),
    ),
  );
}

/** Переключить персону в выборе: не больше `max`, выбранные — в порядке выбора. */
export function toggleSubject(
  selected: SubjectOut[],
  subject: SubjectOut,
  max: number,
): SubjectOut[] {
  if (selected.some((s) => s.subject_id === subject.subject_id))
    return selected.filter((s) => s.subject_id !== subject.subject_id);
  if (selected.length >= max) return max === 1 ? [subject] : selected;
  return [...selected, subject];
}

type Props = {
  mode: ModeOut;
  roles: string[];
  max: number;
  pending: boolean;
  onStart: (subjectIds: string[]) => void;
};

export function SubjectPicker({ mode, roles, max, pending, onStart }: Props) {
  const t = useTranslations();
  const dt = useDynamicT();
  const id = useId();
  const icons = useIcons();
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<SubjectOut[]>([]);
  const q = useDebounced(text.trim(), SUBJECT_SEARCH_DEBOUNCE_MS);
  const found = useQuery({
    queryKey: ["subjects", mode.key, q],
    queryFn: ({ signal }) => searchSubjects(mode.key, q, signal),
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  });
  const role = (r: string) => {
    const key = `game.role.${r}`;
    return dt(key, r);
  };
  const results = found.data ?? [];
  const isSelected = (s: SubjectOut) => selected.some((x) => x.subject_id === s.subject_id);

  const Check = icons.owned;
  return (
    <section
      data-domain={mode.domain}
      data-testid="subject-picker"
      aria-label={dt(mode.title_key, mode.key)}
      className="flex flex-col gap-4 pt-4"
    >
      <HubHeader title={dt(mode.title_key, mode.key)} aside={t(`domain.${mode.domain}`)} />
      <p className="px-0.5 text-sm text-ink-muted">
        {t("subjects.lead", { max, roles: roles.map(role).join(", ").toLowerCase() })}
      </p>

      <div>
        <SearchField
          id={`${id}-q`}
          aria-label={t("subjects.searchLabel")}
          value={text}
          maxLength={QUERY_MAX}
          placeholder={t("subjects.searchPlaceholder")}
          onChange={(e) => setText(e.target.value)}
          aria-controls={`${id}-list`}
          aria-busy={found.isFetching}
          data-testid="subject-search"
        />
      </div>

      <div aria-live="polite" className="sr-only">
        {found.isSuccess ? t("subjects.found", { count: results.length }) : ""}
      </div>

      {found.isError ? (
        <p className="text-ink-muted" role="alert">
          {t("subjects.failed")}
        </p>
      ) : found.isSuccess && results.length === 0 ? (
        <p className="text-ink-muted">{t("subjects.empty")}</p>
      ) : (
        <ul
          id={`${id}-list`}
          className="flex flex-col gap-2.5"
          aria-label={t("subjects.listLabel")}
        >
          {results.map((s) => {
            const on = isSelected(s);
            return (
              <li key={s.subject_id}>
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => setSelected((cur) => toggleSubject(cur, s, max))}
                  disabled={!on && selected.length >= max && max > 1}
                  data-testid={`subject-option-${s.subject_id}`}
                  className={cn(
                    "flex min-h-[68px] w-full items-center justify-between gap-4 rounded-[var(--radius-control)] border-[1.5px] px-[18px] py-2 text-left transition-colors disabled:opacity-45",
                    on
                      ? "border-music bg-stage-raised shadow-[0_0_0_4px_var(--color-glow)]"
                      : "border-rule bg-stage hover:border-rule-strong",
                  )}
                >
                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="truncate font-semibold">{s.name}</span>
                    <span className="text-[13px] text-ink-muted">
                      {role(s.role)} · {t("subjects.items", { count: s.items })}
                    </span>
                  </span>
                  {on && <Check aria-hidden className="shrink-0 text-music" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-4 pt-2">
        <Button
          size="lg"
          disabled={selected.length === 0 || pending}
          onClick={() => onStart(selected.map((s) => s.subject_id))}
          data-testid="subject-start"
        >
          {t("subjects.start")}
        </Button>
        <p className="text-sm text-ink-muted" aria-live="polite">
          {selected.length
            ? t("subjects.selected", { names: selected.map((s) => s.name).join(", ") })
            : t("subjects.none")}
        </p>
      </div>
    </section>
  );
}
