"use client";
/** Минимальные формы входа, регистрации и подтверждения e-mail к API identity. */
import { useEffect, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import { Button } from "@/shared/ui/button";
import { Field } from "@/shared/ui/field";
import { useDynamicT } from "@/shared/i18n/dynamic";
import type { ConsentRequirement } from "./consents";

type DynT = ReturnType<typeof useDynamicT>;

/** Текст ошибки identity по коду (`auth.error.<код>`); неизвестный код — общий текст. */
function errorText(dt: DynT, generic: string, e: unknown): string {
  if (isApiError(e) && e.status === 429) return dt("auth.error.rate_limited", generic);
  const code = isApiError(e) ? e.code : "network";
  return dt(`auth.error.${code.replace(/^auth\./, "").replace(/\./g, "_")}`, generic);
}

/** Только относительный путь этого же сайта: защита от открытого редиректа через `?next=`. */
export function safeNext(next: string | null | undefined): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")
    ? next
    : "/";
}

export function LoginForm({ next }: { next: string | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const router = useRouter();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      unwrap(
        await api().identity.POST("/v1/auth/login", {
          body: {
            login: String(form.get("login") ?? ""),
            password: String(form.get("password") ?? ""),
          },
        }),
      );
      await client.invalidateQueries();
      router.replace(safeNext(next));
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
      <Field
        label={t("auth.login")}
        name="login"
        autoComplete="username"
        required
        maxLength={254}
      />
      <Field
        label={t("auth.password")}
        name="password"
        type="password"
        autoComplete="current-password"
        required
        maxLength={256}
      />
      {error && (
        <p role="alert" className="text-miss">
          {error}
        </p>
      )}
      <Button type="submit" size="lg" disabled={busy}>
        {t("auth.signIn")}
      </Button>
      <p className="text-ink-muted">
        {t("auth.noAccount")}{" "}
        <Link href="/register" className="font-medium text-ink underline underline-offset-4">
          {t("auth.signUp")}
        </Link>
      </p>
    </form>
  );
}

export function RegisterForm({ consents }: { consents: ConsentRequirement[] }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const accepted = consents.filter((c) => form.get(`consent-${c.kind}`) === "on");
    if (accepted.length !== consents.length) {
      setError(t("auth.error.consents_required"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      unwrap(
        await api().identity.POST("/v1/auth/register", {
          body: {
            nickname: String(form.get("nickname") ?? "").trim(),
            email: String(form.get("email") ?? "").trim(),
            password: String(form.get("password") ?? ""),
            consents: accepted,
            locale: "ru",
          },
        }),
      );
      setDone(true);
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <p
        role="status"
        className="rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule"
        data-testid="register-done"
      >
        {t("auth.checkEmail")}
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
      <Field
        label={t("auth.nickname")}
        name="nickname"
        autoComplete="nickname"
        required
        maxLength={64}
      />
      <Field
        label={t("auth.email")}
        name="email"
        type="email"
        autoComplete="email"
        required
        maxLength={254}
      />
      <Field
        label={t("auth.password")}
        name="password"
        type="password"
        autoComplete="new-password"
        required
        maxLength={256}
        hint={t("auth.passwordHint")}
      />
      <fieldset className="flex flex-col gap-3">
        <legend className="mb-2 text-sm font-medium">{t("auth.consentsTitle")}</legend>
        {consents.map((c) => (
          <label key={c.kind} className="flex items-start gap-3 text-sm text-ink-muted">
            <input
              type="checkbox"
              name={`consent-${c.kind}`}
              className="mt-0.5 size-5 shrink-0 accent-[var(--color-music)]"
            />
            <span>{dt(`auth.consent.${c.kind}`, c.kind)}</span>
          </label>
        ))}
      </fieldset>
      {error && (
        <p role="alert" className="text-miss">
          {error}
        </p>
      )}
      <Button type="submit" size="lg" disabled={busy}>
        {t("auth.signUp")}
      </Button>
      <p className="text-ink-muted">
        {t("auth.haveAccount")}{" "}
        <Link href="/login" className="font-medium text-ink underline underline-offset-4">
          {t("auth.signIn")}
        </Link>
      </p>
    </form>
  );
}

export function VerifyEmail({ token }: { token: string | null }) {
  const t = useTranslations();
  const [state, setState] = useState<"pending" | "ok" | "failed">(token ? "pending" : "failed");
  const sent = useRef(false);
  useEffect(() => {
    if (!token || sent.current) return;
    sent.current = true;
    api()
      .identity.POST("/v1/auth/verify-email", { body: { token } })
      .then((r) => setState(r.response.ok ? "ok" : "failed"))
      .catch(() => setState("failed"));
  }, [token]);
  return (
    <div className="flex flex-col items-start gap-4" role="status">
      <p className="text-lg" data-testid="verify-state">
        {t(`auth.verify.${state}`)}
      </p>
      {state === "ok" && (
        <Button asChild>
          <Link href="/login">{t("auth.signIn")}</Link>
        </Button>
      )}
    </div>
  );
}
