"use client";
/** Поиск единиц каталога для сборника (catalog autocomplete с паузой) и кнопки «Добавить». */
import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";
import type { Domain, Suggestion } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { useAutocomplete } from "@/features/game/use-autocomplete";
import { itemLiteKey, type ItemLite } from "./ItemList";

type Props = {
  domain: Domain;
  added: Set<string>;
  full: boolean;
  onAdd: (id: string) => void;
};

export function ItemSearch({ domain, added, full, onAdd }: Props) {
  const t = useTranslations();
  const client = useQueryClient();
  const id = useId();
  const [query, setQuery] = useState("");
  const { items, loading } = useAutocomplete(query, domain);

  const add = (s: Suggestion) => {
    // Карточка из подсказки — сразу в кэш: строка состава не ждёт запроса в catalog.
    client.setQueryData<ItemLite>(itemLiteKey(s.id), {
      title: s.title,
      year: s.year,
      subtitle: s.subtitle,
    });
    onAdd(s.id);
  };

  return (
    <div className="flex flex-col gap-3">
      <label htmlFor={id} className="text-sm font-medium">
        {t("collections.editor.search")}
      </label>
      <input
        id={id}
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t(`collections.editor.searchPlaceholder.${domain}`)}
        autoComplete="off"
        data-testid="editor-item-search"
        className="h-12 rounded-[var(--radius-control)] bg-night px-4 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-[var(--accent)]"
      />
      {query.trim() && (
        <ul className="flex flex-col gap-1" aria-live="polite" aria-busy={loading}>
          {items.map((s) => {
            const isAdded = added.has(s.id);
            return (
              <li
                key={s.id}
                className="flex items-center justify-between gap-3 rounded-lg bg-stage px-3 py-2 ring-1 ring-rule"
              >
                <span className="min-w-0">
                  <span dir="auto" className="block truncate font-medium">
                    {s.title}
                  </span>
                  <span dir="auto" className="block truncate text-sm text-ink-muted">
                    {[s.year, s.subtitle].filter(Boolean).join(", ")}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={isAdded || full}
                  onClick={() => add(s)}
                  data-testid={`editor-add-${s.id}`}
                  aria-label={t("collections.editor.addNamed", { title: s.title })}
                >
                  <Plus aria-hidden />
                  {isAdded ? t("collections.editor.added") : t("collections.editor.add")}
                </Button>
              </li>
            );
          })}
          {!loading && items.length === 0 && (
            <li className="px-3 py-2 text-sm text-ink-muted">{t("collections.editor.nothing")}</li>
          )}
        </ul>
      )}
    </div>
  );
}
