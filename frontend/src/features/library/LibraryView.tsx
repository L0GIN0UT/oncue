"use client";
/**
 * Медиатека `/library` (D6, D11): системные полки из конфига и свои (создать, переименовать,
 * удалить). Только пользователю: аноним — 401 и вход, гость — 403 `auth.guest_forbidden` и
 * регистрация. Названия своих полок — UGC (`dir="auto"`, только JSX-экранирование).
 */
import { useId, useState, type FormEvent } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { Pencil, Trash2 } from "lucide-react";
import { isApiError } from "@/shared/api/errors";
import type { ShelfCard } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { inputClass } from "@/shared/ui/field";
import { useToast } from "@/shared/ui/toast";
import { useIcons } from "@/shared/ui/theme-provider";
import { useViewer } from "@/features/auth/session";
import { HubHeader } from "@/features/hubs/HubHeader";
import { apiErrorText } from "@/features/collections/errors";
import { createShelf, deleteShelf, fetchLibraryPage, libraryKeys, renameShelf } from "./api";
import { useShelfTitle } from "./ShelfPicker";

export function LibraryGate({ status }: { status: number | null }) {
  const t = useTranslations();
  const guest = status === 403;
  return (
    <div className="flex flex-col items-start gap-4 pt-8">
      <h1 className="font-display text-[28px] leading-tight font-extrabold tracking-[-0.03em]">
        {t("library.usersOnly.title")}
      </h1>
      <p className="text-ink-muted">{t("library.usersOnly.text")}</p>
      <Button asChild>
        <Link href={guest ? "/register" : "/login"}>
          {guest ? t("auth.signUp") : t("nav.login")}
        </Link>
      </Button>
    </div>
  );
}

export function LibraryView() {
  const t = useTranslations();
  const viewer = useViewer();
  const page = useQuery({
    queryKey: libraryKeys.page,
    queryFn: fetchLibraryPage,
    enabled: viewer.kind === "user",
  });

  if (!viewer.pending && viewer.kind !== "user")
    return <LibraryGate status={viewer.kind === "guest" ? 403 : 401} />;
  if (page.isError) {
    const status = isApiError(page.error) ? page.error.status : null;
    if (status === 401 || status === 403) return <LibraryGate status={status} />;
    return <p className="pt-12 text-ink-muted">{t("library.unavailable")}</p>;
  }
  const block = page.data?.blocks.shelves;
  return (
    <div className="flex flex-col gap-4 pt-4">
      <HubHeader title={t("library.title")} />
      <p className="px-0.5 text-sm text-ink-muted">{t("library.lead")}</p>
      {!block ? (
        <p className="text-ink-muted" role="status">
          {t("common.loading")}
        </p>
      ) : block.status !== "ok" ? (
        <p className="text-ink-muted">{t("library.unavailable")}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {(block.data ?? []).map((shelf) => (
            <ShelfTile key={shelf.ref} shelf={shelf} />
          ))}
        </ul>
      )}
      <NewShelfForm />
    </div>
  );
}

function ShelfTile({ shelf }: { shelf: ShelfCard }) {
  const t = useTranslations();
  const title = useShelfTitle();
  const toast = useToast();
  const client = useQueryClient();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(shelf.title ?? "");
  const [pending, setPending] = useState(false);
  const inputId = useId();
  const custom = shelf.kind === "custom" && shelf.shelf_id;

  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: libraryKeys.page }),
      client.invalidateQueries({ queryKey: libraryKeys.shelves }),
    ]);

  const run = async (action: () => Promise<unknown>) => {
    setPending(true);
    try {
      await action();
      await refresh();
      setRenaming(false);
    } catch (e) {
      toast(apiErrorText(t, e));
    } finally {
      setPending(false);
    }
  };

  return (
    <li className="flex flex-col gap-1">
      {renaming && custom ? (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => renameShelf(shelf.shelf_id!, name.trim()));
          }}
        >
          <label htmlFor={inputId} className="text-sm text-ink-muted">
            {t("library.rename")}
          </label>
          <input
            id={inputId}
            value={name}
            onChange={(e) => setName(e.target.value)}
            dir="auto"
            className={cn(inputClass, "h-11")}
          />
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={pending || !name.trim()}>
              {t("library.save.done")}
            </Button>
            <Button size="sm" variant="ghost" type="button" onClick={() => setRenaming(false)}>
              {t("collections.mine.cancel")}
            </Button>
          </div>
        </form>
      ) : (
        <Link
          href={`/library/${shelf.ref}`}
          data-testid={`shelf-card-${shelf.ref}`}
          className="flex min-h-[60px] items-center justify-between gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-[18px] py-2 transition-colors hover:border-rule-strong"
        >
          <span dir="auto" className="truncate font-semibold">
            {title(shelf)}
          </span>
          <span className="tabular shrink-0 text-sm text-ink-muted">
            <span aria-hidden>{shelf.items_count}</span>
            <span className="sr-only">{t("library.count", { count: shelf.items_count })}</span>
          </span>
        </Link>
      )}
      {custom && !renaming && (
        <div className="flex gap-1 px-2">
          <Button size="sm" variant="ghost" onClick={() => setRenaming(true)}>
            <Pencil aria-hidden />
            {t("library.rename")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={pending}
            onClick={() => {
              if (window.confirm(t("library.deleteConfirm", { title: shelf.title ?? "" })))
                void run(() => deleteShelf(shelf.shelf_id!));
            }}
            data-testid={`shelf-delete-${shelf.ref}`}
          >
            <Trash2 aria-hidden />
            {t("library.delete")}
          </Button>
        </div>
      )}
    </li>
  );
}

function NewShelfForm() {
  const t = useTranslations();
  const Plus = useIcons().plus;
  const toast = useToast();
  const client = useQueryClient();
  const [title, setTitle] = useState("");
  const [pending, setPending] = useState(false);
  const id = useId();
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setPending(true);
    try {
      await createShelf(title.trim());
      setTitle("");
      await Promise.all([
        client.invalidateQueries({ queryKey: libraryKeys.page }),
        client.invalidateQueries({ queryKey: libraryKeys.shelves }),
      ]);
    } catch (err) {
      toast(apiErrorText(t, err));
    } finally {
      setPending(false);
    }
  };
  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-2 pt-2">
      <label htmlFor={id} className="text-sm font-medium">
        {t("library.newShelf")}
      </label>
      <div className="flex gap-2">
        <input
          id={id}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          dir="auto"
          data-testid="shelf-new-title"
          className={cn(inputClass, "min-w-0 flex-1")}
        />
        <Button
          type="submit"
          variant="secondary"
          size="lg"
          disabled={pending || !title.trim()}
          data-testid="shelf-create"
        >
          <Plus aria-hidden size={18} />
          {t("library.create")}
        </Button>
      </div>
    </form>
  );
}
