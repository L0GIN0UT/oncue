"use client";
/**
 * Новый сборник `/collections/new`: домен и название → `POST /v1/collections` (черновик
 * `revision = 1`) → конструктор. Только пользователям (D7); лимит черновиков и ограничение
 * автора приходят ошибками сервера.
 */
import { useId, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import type { Domain } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { useViewer } from "@/features/auth/session";
import { createCollection, useCollectionsMeta } from "../api";
import { apiErrorText } from "../errors";
import { UsersOnly } from "./CollectionEditor";

const DOMAINS: Domain[] = ["film", "music"];

export function NewCollection() {
  const t = useTranslations();
  const router = useRouter();
  const viewer = useViewer();
  const meta = useCollectionsMeta();
  const [domain, setDomain] = useState<Domain>("film");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const titleId = useId();

  if (!viewer.pending && viewer.kind !== "user") return <UsersOnly />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) {
      setError(t("errors.ugc.empty"));
      return;
    }
    setPending(true);
    setError(null);
    try {
      const draft = await createCollection(domain, title.trim());
      router.replace(`/collections/${draft.collection_id}/edit`);
    } catch (err) {
      setError(apiErrorText(t, err));
      setPending(false);
    }
  };

  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="flex max-w-xl flex-col gap-6 pt-6 sm:pt-10"
      data-testid="collection-new"
      noValidate
    >
      <h1 className="font-display text-3xl font-bold">{t("collections.new.title")}</h1>
      <p className="text-ink-muted">{t("collections.new.lead")}</p>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">{t("collections.new.domain")}</legend>
        <div className="flex gap-2">
          {DOMAINS.map((d) => (
            <label
              key={d}
              data-domain={d}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-[var(--radius-control)] px-4 py-3 ring-1 ring-rule",
                domain === d && "ring-2 ring-[var(--accent)]",
              )}
            >
              <input
                type="radio"
                name="domain"
                value={d}
                checked={domain === d}
                onChange={() => setDomain(d)}
                className="accent-[var(--accent)]"
              />
              {t(`domain.${d}`)}
            </label>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={titleId} className="text-sm font-medium">
          {t("collections.editor.name")}
        </label>
        <input
          id={titleId}
          value={title}
          maxLength={meta.data?.limits?.title_max}
          onChange={(e) => setTitle(e.target.value)}
          dir="auto"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${titleId}-error` : undefined}
          data-testid="new-title"
          className="h-12 rounded-[var(--radius-control)] bg-night px-4 text-ink ring-1 ring-rule outline-none ring-inset focus:ring-2 focus:ring-music aria-[invalid=true]:ring-miss"
        />
        {error && (
          <p id={`${titleId}-error`} className="text-sm text-miss" role="alert">
            {error}
          </p>
        )}
      </div>
      <Button type="submit" size="lg" className="self-start" disabled={pending}>
        {t("collections.new.submit")}
      </Button>
    </form>
  );
}
