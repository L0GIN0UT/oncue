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

/** Ключ localStorage: куда вернуть после подтверждения e-mail (ссылка из письма — новая вкладка). */
const PENDING_NEXT_KEY = "gv_auth_next";

/** Ссылка на вход или регистрацию с безопасным `next` (без него, если возвращать некуда). */
export function withNext(path: "/login" | "/register", next: string | null | undefined): string {
  const safe = safeNext(next);
  return safe === "/" ? path : `${path}?next=${encodeURIComponent(safe)}`;
}

/** Запомнить `next` регистрации до подтверждения e-mail (удобство одного браузера). */
export function rememberNext(next: string | null | undefined): void {
  const safe = safeNext(next);
  try {
    if (safe === "/") globalThis.localStorage?.removeItem(PENDING_NEXT_KEY);
    else globalThis.localStorage?.setItem(PENDING_NEXT_KEY, safe);
  } catch {
    // Хранилище недоступно (приватный режим) — вернём на главную.
  }
}

/** Прочитать и забыть запомненный `next`; значение снова проходит {@link safeNext}. */
export function takeRememberedNext(): string | null {
  try {
    const value = globalThis.localStorage?.getItem(PENDING_NEXT_KEY) ?? null;
    globalThis.localStorage?.removeItem(PENDING_NEXT_KEY);
    const safe = safeNext(value);
    return safe === "/" ? null : safe;
  } catch {
    return null;
  }
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
  /** Куда вернуть после подтверждения e-mail и входа (`?next=`, безопасный путь сайта). */
  next?: string | null;
}) {
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
      rememberNext(next);
      setDone(true);
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <div className="flex flex-col items-start gap-4">
        <p
          role="status"
          className="rounded-[var(--radius-ticket)] bg-stage p-6 ring-1 ring-rule"
          data-testid="register-done"
        >
          {t("auth.checkEmail")}
        </p>
        <Button asChild variant="secondary">
          <Link href={withNext("/login", next)} data-testid="register-done-login">
            {t("auth.signIn")}
          </Link>
        </Button>
      </div>
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

export type VerifyState = "pending" | "ok" | "failed" | "rate_limited";

/**
 * Подтвердить e-mail. 429 (лимит gateway `auth_per_ip`) — не «недействительная ссылка»:
 * токен не израсходован, поэтому повторяем с паузой; не отпустило — `rate_limited`.
 */
export async function verifyEmailToken(
  send: () => Promise<{ status: number; retryAfterS?: number }>,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
  attempts = 4,
): Promise<VerifyState> {
  for (let i = 0; i < attempts; i++) {
    const { status, retryAfterS } = await send();
    if (status !== 429) return status >= 200 && status < 300 ? "ok" : "failed";
    if (i < attempts - 1) await sleep(Math.min(Math.max(retryAfterS ?? 3, 1), 10) * 1000);
  }
  return "rate_limited";
}

export function VerifyEmail({ token }: { token: string | null }) {
  const t = useTranslations();
  const [state, setState] = useState<VerifyState>(token ? "pending" : "failed");
  const [next, setNext] = useState<string | null>(null);
  const sent = useRef(false);
  useEffect(() => {
    if (!token || sent.current) return;
    sent.current = true;
    verifyEmailToken(async () => {
      const r = await api().identity.POST("/v1/auth/verify-email", { body: { token } });
      const retryAfter = Number(r.response.headers.get("Retry-After"));
      return {
        status: r.response.status,
        retryAfterS: Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined,
      };
    })
      .then((result) => {
        if (result === "ok") setNext(takeRememberedNext());
        setState(result);
      })
      .catch(() => setState("failed"));
  }, [token]);
  return (
    <div className="flex flex-col items-start gap-4" role="status">
      <p className="text-lg" data-testid="verify-state">
        {t(`auth.verify.${state}`)}
      </p>
      {state === "ok" && (
        <Button asChild>
          <Link href={withNext("/login", next)} data-testid="verify-login">
            {t("auth.signIn")}
          </Link>
        </Button>
      )}
    </div>
  );
}
