"use client";
/**
 * Конструктор сборника `/collections/{id}/edit` (D2): название, описание, теги и обложка из
 * `GET /v1/meta`, режим, состав (поиск в catalog, порядок, играбельность, свои эмодзи),
 * автосохранение с экраном конфликта и публикация с ошибками по полям.
 */
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { isApiError } from "@/shared/api/errors";
import type { DraftOut, MetaLimits, TagOut } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useToast } from "@/shared/ui/toast";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { useViewer } from "@/features/auth/session";
import { collectionKeys, fetchDraft, fetchPlayability, useCollectionsMeta } from "../api";
import { coverGradient } from "../Cover";
import { apiErrorText, errorText, fieldOf } from "../errors";
import { useCollectionModes } from "../modes";
import { ConflictScreen } from "./ConflictScreen";
import { isDirty } from "./draft";
import { ItemList } from "./ItemList";
import { ItemSearch } from "./ItemSearch";
import { useDraftEditor } from "./use-draft-editor";

/** Пауза перед проверкой играбельности после правки состава. */
const PLAYABILITY_DEBOUNCE_MS = 600;

export function CollectionEditorPage({ collectionId }: { collectionId: string }) {
  const t = useTranslations();
  const viewer = useViewer();
  const draft = useQuery({
    queryKey: collectionKeys.draft(collectionId),
    queryFn: () => fetchDraft(collectionId),
    enabled: viewer.kind === "user",
    // Черновик загружается один раз: дальше источник правды — состояние конструктора.
    staleTime: Infinity,
    gcTime: 0,
  });

  if (!viewer.pending && viewer.kind !== "user") return <UsersOnly />;
  if (draft.error) {
    const missing = isApiError(draft.error) && draft.error.status === 404;
    return (
      <div className="flex flex-col items-start gap-4 pt-12" role="alert">
        <h1 className="font-display text-2xl font-bold">
          {t(missing ? "collections.notFound" : "collections.editor.loadFailed")}
        </h1>
        <Button asChild variant="secondary">
          <Link href="/me/collections">{t("collections.mine.title")}</Link>
        </Button>
      </div>
    );
  }
  if (!draft.data)
    return (
      <p className="pt-12 text-ink-muted" role="status">
        {t("common.loading")}
      </p>
    );
  return <CollectionEditor key={draft.data.collection_id} draft={draft.data} />;
}

export function UsersOnly() {
  const t = useTranslations();
  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12">
      <h1 className="font-display text-2xl font-bold">{t("collections.usersOnly.title")}</h1>
      <p className="text-ink-muted">{t("collections.usersOnly.text")}</p>
      <div className="flex gap-3">
        <Button asChild>
          <Link href="/register">{t("auth.signUp")}</Link>
        </Button>
        <Button asChild variant="secondary">
          <Link href="/login">{t("nav.login")}</Link>
        </Button>
      </div>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setV(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return v;
}

/** Играбельные единицы в режиме (game `playability`, без названий). */
function usePlayability(modeKey: string | null | undefined, ids: string[]) {
  const key = useDebounced(ids.join(","), PLAYABILITY_DEBOUNCE_MS);
  const q = useQuery({
    queryKey: ["collections", "playability", modeKey, key],
    queryFn: async () => new Set(await fetchPlayability(modeKey!, key.split(","))),
    enabled: Boolean(modeKey && key),
    staleTime: 60_000,
    retry: false,
  });
  return q.data ?? null;
}

export function CollectionEditor({
  draft,
  debounceMs,
}: {
  draft: DraftOut;
  /** Пауза автосохранения (тесты укорачивают). */
  debounceMs?: number;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const toast = useToast();
  const meta = useCollectionsMeta();
  const editor = useDraftEditor(draft, debounceMs);
  const { state, dispatch } = editor;
  const body = state.body;
  const items = useMemo(() => body.items ?? [], [body.items]);
  const limits: MetaLimits | null = meta.data?.limits ?? null;
  const modes = useCollectionModes(state.domain, body.default_mode_key);
  const playable = usePlayability(
    body.default_mode_key,
    items.map((i) => i.media_item_id),
  );
  const [busy, setBusy] = useState(false);
  const titleId = useId();
  const descriptionId = useId();
  const modeId = useId();
  const visibilityId = useId();

  const errorsOf = (kind: string) => state.errors.filter((e) => fieldOf(e.path).kind === kind);
  const fieldError = (kind: string) =>
    errorsOf(kind)
      .map((e) => errorText(t, e))
      .join(". ") || null;

  const tags = (meta.data?.tags ?? []).filter((tag) => tag.domains.includes(state.domain));
  const selectedTags = body.tags ?? [];
  const tagsMax = limits?.tags_max ?? Infinity;

  const toggleTag = (tag: TagOut) => {
    const on = selectedTags.includes(tag.key);
    if (!on && selectedTags.length >= tagsMax) return;
    dispatch({
      type: "edit",
      patch: { tags: on ? selectedTags.filter((k) => k !== tag.key) : [...selectedTags, tag.key] },
    });
  };

  const onPublish = async () => {
    setBusy(true);
    try {
      const outcome = await editor.publish();
      if (outcome === "published") {
        toast(t("collections.editor.published"));
        router.push(`/collections/${state.collectionId}`);
      } else if (outcome === "invalid") {
        toast(t("collections.editor.publishInvalid"));
      } else if (outcome === "failed") {
        toast(apiErrorText(t, editor.lastError));
      }
    } finally {
      setBusy(false);
    }
  };

  const dirty = isDirty(state);
  const saveStatus = state.conflict
    ? t("collections.editor.status.conflict")
    : state.inflight !== null
      ? t("collections.editor.status.saving")
      : state.saveError
        ? t("collections.editor.status.failed")
        : dirty
          ? t("collections.editor.status.dirty")
          : state.revision > 0
            ? t("collections.editor.status.saved")
            : t("collections.editor.status.published");

  return (
    <div data-domain={state.domain} className="flex flex-col gap-8 pt-6 sm:pt-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1">
          <p className="text-[var(--accent)]">{t(`domain.${state.domain}`)}</p>
          <h1 className="font-display text-3xl font-bold">{t("collections.editor.title")}</h1>
        </div>
        <p className="text-sm text-ink-muted" role="status" data-testid="editor-save-status">
          {saveStatus}
        </p>
      </header>

      {state.status === "hidden" && (
        <p className="rounded-xl bg-miss/15 px-4 py-3 text-miss ring-1 ring-miss" role="alert">
          {t("collections.hiddenByModerator")}
        </p>
      )}

      {state.conflict && (
        <ConflictScreen
          mine={body}
          theirs={state.conflict}
          busy={busy}
          onKeepMine={() => {
            setBusy(true);
            void editor.keepMine().finally(() => setBusy(false));
          }}
          onTakeTheirs={editor.takeTheirs}
        />
      )}

      {state.saveError && !state.conflict && (
        <p className="text-sm text-miss" role="alert" data-testid="editor-save-error">
          {apiErrorText(t, editor.lastError)}
        </p>
      )}

      {state.errors.length > 0 && (
        <div
          role="alert"
          className="flex flex-col gap-1 rounded-xl bg-miss/10 px-4 py-3 ring-1 ring-miss"
          data-testid="editor-errors"
        >
          <p className="font-medium">{t("collections.editor.publishInvalid")}</p>
          <ul className="list-disc pl-5 text-sm">
            {state.errors.map((e) => (
              <li key={`${e.path}:${e.code}`} data-error-path={e.path} data-error-code={e.code}>
                {errorText(t, e)}
              </li>
            ))}
          </ul>
        </div>
      )}

      <fieldset disabled={Boolean(state.conflict)} className="flex flex-col gap-8">
        <section className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_16rem]">
          <div className="flex flex-col gap-5">
            <TextField
              id={titleId}
              label={t("collections.editor.name")}
              error={fieldError("title")}
            >
              <input
                id={titleId}
                value={body.title}
                maxLength={limits?.title_max}
                onChange={(e) => dispatch({ type: "edit", patch: { title: e.target.value } })}
                dir="auto"
                data-testid="editor-title"
                aria-invalid={errorsOf("title").length ? true : undefined}
                className={inputClass}
              />
            </TextField>
            <TextField
              id={descriptionId}
              label={t("collections.editor.description")}
              error={fieldError("description")}
            >
              <textarea
                id={descriptionId}
                value={body.description ?? ""}
                maxLength={limits?.description_max}
                rows={3}
                onChange={(e) =>
                  dispatch({ type: "edit", patch: { description: e.target.value || null } })
                }
                dir="auto"
                data-testid="editor-description"
                aria-invalid={errorsOf("description").length ? true : undefined}
                className={cn(inputClass, "h-auto py-3")}
              />
            </TextField>
            <div className="grid gap-5 sm:grid-cols-2">
              <TextField
                id={modeId}
                label={t("collections.editor.mode")}
                error={fieldError("mode")}
              >
                <select
                  id={modeId}
                  value={body.default_mode_key ?? ""}
                  onChange={(e) =>
                    dispatch({ type: "edit", patch: { default_mode_key: e.target.value || null } })
                  }
                  data-testid="editor-mode"
                  className={inputClass}
                >
                  <option value="">{t("collections.editor.modeNone")}</option>
                  {modes.map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.titleKey ? dt(m.titleKey, m.key) : m.key}
                    </option>
                  ))}
                </select>
              </TextField>
              <TextField id={visibilityId} label={t("collections.editor.visibility")}>
                <select
                  id={visibilityId}
                  value={body.visibility}
                  onChange={(e) =>
                    dispatch({
                      type: "edit",
                      patch: { visibility: e.target.value === "unlisted" ? "unlisted" : "public" },
                    })
                  }
                  className={inputClass}
                >
                  <option value="public">{t("collections.visibility.public")}</option>
                  <option value="unlisted">{t("collections.visibility.unlisted")}</option>
                </select>
              </TextField>
            </div>
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium">{t("collections.editor.cover")}</legend>
            <div className="grid grid-cols-4 gap-2" data-testid="editor-covers">
              {(meta.data?.cover_presets ?? []).map((p) => {
                const on = body.cover?.preset === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    aria-pressed={on}
                    aria-label={t("collections.editor.coverNamed", { id: p.id })}
                    onClick={() =>
                      dispatch({ type: "edit", patch: { cover: on ? null : { preset: p.id } } })
                    }
                    className={cn(
                      "grid aspect-square place-items-center rounded-lg text-xl ring-1 ring-rule",
                      on && "ring-2 ring-ink",
                    )}
                    style={{ background: coverGradient(p) }}
                  >
                    <span aria-hidden>{p.emoji}</span>
                  </button>
                );
              })}
            </div>
            {fieldError("cover") && <p className="text-sm text-miss">{fieldError("cover")}</p>}
          </fieldset>
        </section>

        <fieldset className="flex flex-col gap-3">
          <legend className="mb-2 text-sm font-medium">
            {t("collections.editor.tags", { max: limits?.tags_max ?? 0 })}
          </legend>
          <div className="flex flex-wrap gap-2">
            {tags.map((tag) => {
              const on = selectedTags.includes(tag.key);
              return (
                <button
                  key={tag.key}
                  type="button"
                  aria-pressed={on}
                  disabled={!on && selectedTags.length >= tagsMax}
                  onClick={() => toggleTag(tag)}
                  className={cn(
                    "rounded-full px-3 py-1.5 text-sm ring-1 ring-rule disabled:opacity-40",
                    on ? "bg-[var(--accent)] text-night ring-transparent" : "text-ink-muted",
                  )}
                >
                  {dt(tag.title_key, tag.key)}
                </button>
              );
            })}
          </div>
          {fieldError("tags") && <p className="text-sm text-miss">{fieldError("tags")}</p>}
        </fieldset>

        <section className="flex flex-col gap-4" aria-labelledby="editor-items-title">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="editor-items-title" className="font-display text-xl font-bold">
              {t("collections.editor.items")}
            </h2>
            <p className="tabular text-sm text-ink-muted">
              {t("collections.editor.itemsRange", {
                count: items.length,
                min: limits?.items_min ?? 0,
                max: limits?.items_max ?? 0,
              })}
            </p>
          </div>
          {fieldError("items") && (
            <p className="text-sm text-miss" role="alert">
              {fieldError("items")}
            </p>
          )}
          <p id="editor-handle-help" className="sr-only">
            {t("collections.editor.handleHelp")}
          </p>
          <ItemSearch
            domain={state.domain}
            added={new Set(items.map((i) => i.media_item_id))}
            full={limits ? items.length >= limits.items_max : false}
            onAdd={(id) => dispatch({ type: "addItem", mediaItemId: id })}
          />
          <ItemList
            items={items}
            limits={limits}
            playable={body.default_mode_key ? playable : null}
            errors={state.errors.filter((e) => fieldOf(e.path).kind === "item")}
            onMove={(from, to) => dispatch({ type: "moveItem", from, to })}
            onRemove={(index) => dispatch({ type: "removeItem", index })}
            onEmoji={(index, emoji) => dispatch({ type: "setEmoji", index, emoji })}
          />
        </section>
      </fieldset>

      <div className="flex flex-wrap gap-3">
        <Button
          size="lg"
          onClick={() => void onPublish()}
          disabled={busy || Boolean(state.conflict) || (!dirty && state.revision === 0)}
          data-testid="editor-publish"
        >
          {t("collections.editor.publish")}
        </Button>
        <Button asChild size="lg" variant="secondary">
          <Link href="/me/collections">{t("collections.mine.title")}</Link>
        </Button>
      </div>
    </div>
  );
}

const inputClass =
  "h-12 w-full rounded-[var(--radius-control)] bg-night px-4 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-[var(--accent)] aria-[invalid=true]:ring-miss";

function TextField({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {children}
      {error && <p className="text-sm text-miss">{error}</p>}
    </div>
  );
}
