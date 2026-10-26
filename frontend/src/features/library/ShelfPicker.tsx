"use client";
/**
 * «Сохранить → куда»: меню полок единицы (D12). Отметка ставится оптимистично и сразу отражает
 * `exclusive_with` (единица уходит с исключающей полки), ответ сервера (`shelves`) её уточняет,
 * ошибка — откат и тост. Клавиатура: стрелки — по пунктам, Escape — закрыть (фокус на кнопку).
 */
import { useEffect, useRef, type KeyboardEvent } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Check } from "lucide-react";
import type { Domain, ItemShelves, SaveSource, ShelfOut } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { apiErrorText } from "@/features/collections/errors";
import {
  fetchItemShelves,
  fetchShelves,
  libraryKeys,
  optimisticShelves,
  toggleShelfItem,
} from "./api";

type Props = {
  mediaItemId: string;
  domain?: Domain | null;
  source: SaveSource;
  onClose: () => void;
  labelledBy: string;
};

/** Название полки: системной — по `title_key`, своей — текст пользователя. */
export function useShelfTitle(): (shelf: Pick<ShelfOut, "title" | "title_key" | "key">) => string {
  const dt = useDynamicT();
  return (shelf) => shelf.title ?? dt(shelf.title_key ?? "", shelf.key ?? "");
}

export function ShelfPicker({ mediaItemId, domain, source, onClose, labelledBy }: Props) {
  const t = useTranslations();
  const toast = useToast();
  const client = useQueryClient();
  const title = useShelfTitle();
  const menu = useRef<HTMLDivElement>(null);
  const shelves = useQuery({
    queryKey: libraryKeys.shelves,
    queryFn: fetchShelves,
    staleTime: 60_000,
  });
  const itemKey = libraryKeys.item(mediaItemId);
  const item = useQuery({ queryKey: itemKey, queryFn: () => fetchItemShelves(mediaItemId) });
  const list = (shelves.data ?? []).filter((s) => !domain || s.domains.includes(domain));
  const ready = shelves.isSuccess && item.isSuccess;

  useEffect(() => {
    if (ready) menu.current?.querySelector<HTMLElement>("[role=menuitemcheckbox]")?.focus();
  }, [ready]);

  const toggle = async (shelf: ShelfOut) => {
    const before = client.getQueryData<ItemShelves>(itemKey);
    const current = before?.shelves ?? [];
    const on = !current.includes(shelf.ref);
    client.setQueryData<ItemShelves>(itemKey, {
      media_item_id: mediaItemId,
      shelves: optimisticShelves(current, shelf, on),
    });
    try {
      const res = await toggleShelfItem(shelf.ref, mediaItemId, on, source);
      client.setQueryData<ItemShelves>(itemKey, {
        media_item_id: mediaItemId,
        shelves: res.shelves,
      });
      if (res.changed) {
        void client.invalidateQueries({ queryKey: libraryKeys.shelves });
        void client.invalidateQueries({ queryKey: libraryKeys.page });
        void client.invalidateQueries({ queryKey: ["library", "shelf"] });
      }
    } catch (e) {
      if (before) client.setQueryData(itemKey, before);
      toast(apiErrorText(t, e));
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...(menu.current?.querySelectorAll<HTMLElement>("[role=menuitemcheckbox]") ?? []),
    ];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    }
  };

  const checked = new Set(item.data?.shelves ?? []);
  return (
    <div
      ref={menu}
      onKeyDown={onKeyDown}
      data-testid="shelf-picker"
      className="absolute top-full left-0 z-30 mt-2 flex w-64 flex-col gap-1 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage-raised p-2 text-ink"
    >
      {!ready ? (
        <p className="px-3 py-2 text-sm text-ink-muted" role="status">
          {shelves.isError || item.isError ? t("library.picker.failed") : t("common.loading")}
        </p>
      ) : (
        <div role="menu" aria-labelledby={labelledBy} className="flex flex-col gap-1">
          {list.map((shelf) => {
            const on = checked.has(shelf.ref);
            return (
              <button
                key={shelf.ref}
                type="button"
                role="menuitemcheckbox"
                aria-checked={on}
                data-testid={`shelf-option-${shelf.ref}`}
                onClick={() => void toggle(shelf)}
                className={cn(
                  "flex min-h-11 items-center justify-between gap-3 rounded-[10px] px-3 py-2 text-left text-sm hover:bg-rule/60 focus:bg-rule/60",
                  on && "text-ink",
                )}
              >
                <span dir="auto" className="truncate">
                  {title(shelf)}
                </span>
                <Check
                  aria-hidden
                  className={cn("size-4 shrink-0", on ? "opacity-100" : "opacity-0")}
                />
              </button>
            );
          })}
        </div>
      )}
      <Link
        href="/library"
        className="mt-1 flex min-h-11 items-center rounded-[10px] px-3 py-2 text-xs text-ink-muted hover:text-ink"
      >
        {t("library.picker.manage")}
      </Link>
    </div>
  );
}
