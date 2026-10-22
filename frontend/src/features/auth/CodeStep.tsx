"use client";
/**
 * Общий шаг ввода одноразового кода из письма (ADR 0041): регистрация, вход с нового устройства,
 * сброс и смена пароля. Код живёт только в поле формы и исчезает вместе с компонентом.
 */
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { components } from "@/shared/api/schema/identity";
import { Button } from "@/shared/ui/button";
import { Field } from "@/shared/ui/field";

export type Challenge = components["schemas"]["ChallengeResponse"];

type Problem =
  | { kind: "invalid"; left: number | null }
  | { kind: "expired" }
  | { kind: "limit" }
  | { kind: "rate_limited"; seconds: number | null }
  | { kind: "other"; code: string };

function toProblem(e: unknown): Problem {
  if (isApiError(e)) {
    if (e.code === "auth.code_limit_exceeded") return { kind: "limit" };
    if (e.status === 429) return { kind: "rate_limited", seconds: e.retryAfterS };
    if (e.code === "auth.code_invalid") {
      const left = e.details?.attempts_left;
      return { kind: "invalid", left: typeof left === "number" ? left : null };
    }
    if (e.code === "auth.code_expired") return { kind: "expired" };
    if (e.code === "auth.code_resend_too_early")
      return { kind: "rate_limited", seconds: e.retryAfterS };
    return { kind: "other", code: e.code };
  }
  return { kind: "other", code: "network" };
}

/** Секунды, оставшиеся до `deadline` (мс, `Date.now()`); «сейчас» обновляется раз в секунду. */
function useCountdown(deadline: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    // Дедлайн мог смениться между тиками — сразу сверяем часы (в колбэке, не в теле эффекта).
    const sync = setTimeout(() => setNow(Date.now()), 0);
    const id = setInterval(() => {
      const n = Date.now();
      setNow(n);
      if (n >= deadline) clearInterval(id);
    }, 1000);
    return () => {
      clearTimeout(sync);
      clearInterval(id);
    };
  }, [deadline]);
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

export function CodeStep({
  challenge: initial,
  onSubmit,
  submitLabel,
  extra,
}: {
  challenge: Challenge;
  /**
   * Проверить код. Бросает ошибку API при неудаче; успех обрабатывает вызывающий (редирект и т. д.).
   * `form` — значения `extra`-полей (например, новый пароль).
   */
  onSubmit: (code: string, challenge: Challenge, form: FormData) => Promise<void>;
  submitLabel: string;
  /** Дополнительные поля формы рядом с кодом (новый пароль). */
  extra?: ReactNode;
}) {
  const t = useTranslations();
  const [challenge, setChallenge] = useState(initial);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [resendAt, setResendAt] = useState(() => Date.now() + initial.resend_after_s * 1000);
  const [blockedUntil, setBlockedUntil] = useState(0);
  const resendIn = useCountdown(resendAt);
  const blockedFor = useCountdown(blockedUntil);
  const locked = problem?.kind === "limit";

  const block = (p: Problem) => {
    if (p.kind === "rate_limited" && p.seconds) setBlockedUntil(Date.now() + p.seconds * 1000);
  };

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    if (code.length !== challenge.code_length || !/^\d+$/.test(code)) {
      setProblem({ kind: "invalid", left: null });
      return;
    }
    setBusy(true);
    setProblem(null);
    setNotice(null);
    try {
      await onSubmit(code, challenge, form);
    } catch (err) {
      const p = toProblem(err);
      setProblem(p);
      block(p);
      setCode("");
      setBusy(false);
    }
  };

  const resend = async () => {
    setBusy(true);
    setProblem(null);
    setNotice(null);
    try {
      const next = unwrap(
        await api().identity.POST("/v1/auth/codes/resend", {
          body: { challenge_id: challenge.challenge_id },
        }),
      );
      setChallenge(next);
      setCode("");
      setResendAt(Date.now() + next.resend_after_s * 1000);
      setNotice(t("auth.code.resent"));
    } catch (err) {
      const p = toProblem(err);
      setProblem(p);
      if (isApiError(err) && err.retryAfterS) setResendAt(Date.now() + err.retryAfterS * 1000);
    } finally {
      setBusy(false);
    }
  };

  const message = (() => {
    if (!problem) return null;
    switch (problem.kind) {
      case "invalid":
        return problem.left === null
          ? t("auth.code.error.invalid")
          : t("auth.code.error.invalidLeft", { left: problem.left });
      case "expired":
        return t("auth.code.error.expired");
      case "limit":
        return t("auth.code.error.limit");
      case "rate_limited":
        return problem.seconds
          ? t("auth.code.error.rateLimitedIn", { seconds: problem.seconds })
          : t("auth.code.error.rateLimited");
      default:
        return problem.code === "network"
          ? t("auth.code.error.network")
          : t("auth.code.error.generic");
    }
  })();

  return (
    <form onSubmit={submit} className="flex flex-col gap-5" noValidate data-testid="code-step">
      <p className="text-ink-muted">
        {t("auth.code.lead", {
          length: challenge.code_length,
          minutes: Math.ceil(challenge.expires_in_s / 60),
        })}
      </p>
      <Field
        label={t("auth.code.label")}
        name="code"
        value={code}
        onChange={(e) =>
          setCode(e.target.value.replace(/[^0-9]/g, "").slice(0, challenge.code_length))
        }
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        required
        disabled={locked}
        autoFocus
        data-testid="code-input"
        className="text-center font-mono text-2xl tracking-[0.5em]"
      />
      {extra}
      <div aria-live="polite" role="status" className="min-h-6">
        {notice && <p className="text-ink-muted">{notice}</p>}
      </div>
      <div aria-live="assertive" role="alert" className="min-h-0">
        {message && (
          <p className="text-miss" data-testid="code-error" data-kind={problem?.kind}>
            {message}
          </p>
        )}
      </div>
      <Button
        type="submit"
        size="lg"
        disabled={busy || locked || blockedFor > 0}
        data-testid="code-submit"
      >
        {blockedFor > 0 ? t("auth.code.submitWait", { seconds: blockedFor }) : submitLabel}
      </Button>
      <Button
        type="button"
        variant="secondary"
        onClick={resend}
        disabled={busy || resendIn > 0}
        data-testid="code-resend"
      >
        {resendIn > 0 ? t("auth.code.resendIn", { seconds: resendIn }) : t("auth.code.resend")}
      </Button>
    </form>
  );
}
