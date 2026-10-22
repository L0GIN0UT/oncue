"use client";
/** Формы входа и регистрации к API identity; коды из писем вводятся в {@link CodeStep}. */
import { useState, type FormEvent } from "react";
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
import { CodeStep, type Challenge } from "./CodeStep";

export type DynT = ReturnType<typeof useDynamicT>;

/** Текст ошибки identity по коду (`auth.error.<код>`); неизвестный код — общий текст. */
export function errorText(dt: DynT, generic: string, e: unknown): string {
  if (isApiError(e) && e.status === 429) return dt("auth.error.rate_limited", generic);
  const code = isApiError(e) ? e.code : "network";
  return dt(`auth.error.${code.replace(/^auth\./, "").replace(/\./g, "_")}`, generic);
}

// Управляющие символы и обратная косая черта: парсер URL выбрасывает \t\n\r и читает «\»
// как «/», поэтому «/\t/evil.com» или «/\\evil.com» превращаются в «//evil.com».
const UNSAFE_CHARS = /[\u0000-\u001f\u007f\\]/;

/**
 * Куда вернуть после входа (`?next=`): только путь этого же сайта — защита от открытого
 * редиректа. Путь разбирается как URL относительно своего origin; результат —
 * `pathname + search + hash`, иначе `/`.
 */
export function safeNext(
  next: string | null | undefined,
  origin: string = globalThis.location?.origin ?? "http://localhost",
): string {
  if (!next || !next.startsWith("/") || UNSAFE_CHARS.test(next)) return "/";
  let decoded: string;
  let url: URL;
  try {
    decoded = decodeURIComponent(next);
    url = new URL(next, origin);
  } catch {
    return "/";
  }
  if (UNSAFE_CHARS.test(decoded) || url.origin !== origin) return "/";
  return url.pathname + url.search + url.hash;
}

/** Ссылка на вход или регистрацию с безопасным `next` (без него, если возвращать некуда). */
export function withNext(path: "/login" | "/register", next: string | null | undefined): string {
  const safe = safeNext(next);
  return safe === "/" ? path : `${path}?next=${encodeURIComponent(safe)}`;
}

/** Сессия открыта (вход, подтверждение): обновить личные данные и вернуть на `next`. */
export function useFinishSignIn(next: string | null): () => Promise<void> {
  const router = useRouter();
  const client = useQueryClient();
  return async () => {
    await client.invalidateQueries();
    router.replace(safeNext(next));
  };
}

export function LoginForm({ next }: { next: string | null }) {
  const t = useTranslations();
  const dt = useDynamicT();
  const finish = useFinishSignIn(next);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const res = unwrap(
        await api().identity.POST("/v1/auth/login", {
          body: {
            login: String(form.get("login") ?? ""),
            password: String(form.get("password") ?? ""),
          },
        }),
      );
      if (res.status === "code_required" && res.challenge) {
        // Новое устройство: сессии ещё нет, нужен код из письма.
        setChallenge(res.challenge);
        setBusy(false);
        return;
      }
      await finish();
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
      setBusy(false);
    }
  };

  if (challenge) {
    return (
      <CodeStep
        challenge={challenge}
        submitLabel={t("auth.confirmSignIn")}
        onSubmit={async (code, ch) => {
          const body = { challenge_id: ch.challenge_id, code };
          // Неподтверждённому пользователю вход выдаёт код регистрации (ADR 0041) —
          // его принимает verify-email, а не login/confirm.
          unwrap(
            ch.purpose === "register"
              ? await api().identity.POST("/v1/auth/verify-email", { body })
              : await api().identity.POST("/v1/auth/login/confirm", { body }),
          );
          await finish();
        }}
      />
    );
  }

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
      <p>
        <Link
          href="/forgot-password"
          className="text-ink-muted underline underline-offset-4 hover:text-ink"
          data-testid="forgot-link"
        >
          {t("auth.forgot")}
        </Link>
      </p>
      <p className="text-ink-muted">
        {t("auth.noAccount")}{" "}
        <Link
          href={withNext("/register", next)}
          className="font-medium text-ink underline underline-offset-4"
        >
          {t("auth.signUp")}
        </Link>
      </p>
    </form>
  );
}

export function RegisterForm({
  consents,
  next = null,
}: {
  consents: ConsentRequirement[];
  /** Куда вернуть после подтверждения e-mail (`?next=`, безопасный путь сайта). */
  next?: string | null;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const finish = useFinishSignIn(next);

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
      const res = unwrap(
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
      setChallenge(res);
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
    } finally {
      setBusy(false);
    }
  };

  if (challenge) {
    return (
      <CodeStep
        challenge={challenge}
        submitLabel={t("auth.confirmSignUp")}
        onSubmit={async (code, ch) => {
          unwrap(
            await api().identity.POST("/v1/auth/verify-email", {
              body: { challenge_id: ch.challenge_id, code },
            }),
          );
          await finish();
        }}
      />
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
        <Link
          href={withNext("/login", next)}
          className="font-medium text-ink underline underline-offset-4"
        >
          {t("auth.signIn")}
        </Link>
      </p>
    </form>
  );
}
