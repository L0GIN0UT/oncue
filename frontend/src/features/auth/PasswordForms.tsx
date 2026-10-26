"use client";
/** Сброс пароля («забыл») и смена пароля в профиле: оба шага подтверждаются кодом из письма. */
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { Button } from "@/shared/ui/button";
import { Field } from "@/shared/ui/field";
import { useDynamicT } from "@/shared/i18n/dynamic";
import { errorText, useFinishSignIn } from "./AuthForms";
import { CodeStep, type Challenge } from "./CodeStep";

function NewPasswordField({ name }: { name: string }) {
  const t = useTranslations();
  return (
    <Field
      label={t("auth.newPassword")}
      name={name}
      type="password"
      autoComplete="new-password"
      required
      maxLength={256}
      hint={t("auth.passwordHint")}
    />
  );
}

function Done({ text, testId, children }: { text: string; testId: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-4">
      <p
        role="status"
        className="rounded-[var(--radius-ticket)] bg-stage p-6 border-[1.5px] border-rule"
        data-testid={testId}
      >
        {text}
      </p>
      {children}
    </div>
  );
}

/** Первый шаг обеих форм: одно поле → запрос → `ChallengeResponse`. */
function StartForm({
  field,
  submitLabel,
  start,
  onChallenge,
}: {
  field: ReactNode;
  submitLabel: string;
  start: (form: FormData) => Promise<Challenge>;
  onChallenge: (c: Challenge) => void;
}) {
  const t = useTranslations();
  const dt = useDynamicT();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      onChallenge(await start(form));
    } catch (err) {
      setError(errorText(dt, t("auth.error.generic"), err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
      {field}
      {error && (
        <p role="alert" className="text-miss">
          {error}
        </p>
      )}
      <Button type="submit" size="lg" disabled={busy}>
        {submitLabel}
      </Button>
    </form>
  );
}

/**
 * «Забыл пароль». Ответ `forgot` одинаков для любого адреса, поэтому UI всегда переходит к вводу
 * кода и ничем не намекает, есть ли такой аккаунт.
 */
export function ForgotPasswordForm() {
  const t = useTranslations();
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const finish = useFinishSignIn(null);

  if (challenge) {
    return (
      <CodeStep
        challenge={challenge}
        submitLabel={t("auth.resetSubmit")}
        extra={<NewPasswordField name="password" />}
        onSubmit={async (code, ch, form) => {
          unwrap(
            await api().identity.POST("/v1/auth/password/reset", {
              body: {
                challenge_id: ch.challenge_id,
                code,
                password: String(form.get("password") ?? ""),
              },
            }),
          );
          // Код из письма подтвердил владение: сервер сразу открыл сессию и доверил устройство.
          await finish();
        }}
      />
    );
  }
  return (
    <StartForm
      field={
        <Field
          label={t("auth.email")}
          name="email"
          type="email"
          autoComplete="email"
          required
          maxLength={254}
        />
      }
      submitLabel={t("auth.forgotSubmit")}
      start={async (form) =>
        unwrap(
          await api().identity.POST("/v1/auth/password/forgot", {
            body: { email: String(form.get("email") ?? "").trim() },
          }),
        )
      }
      onChallenge={setChallenge}
    />
  );
}

/** Смена пароля в профиле: текущий пароль → код из письма + новый пароль. */
export function ChangePasswordForm() {
  const t = useTranslations();
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [done, setDone] = useState(false);

  if (done) return <Done text={t("auth.security.done")} testId="change-done" />;
  if (challenge) {
    return (
      <CodeStep
        challenge={challenge}
        submitLabel={t("auth.resetSubmit")}
        extra={<NewPasswordField name="new_password" />}
        onSubmit={async (code, ch, form) => {
          unwrap(
            await api().identity.POST("/v1/auth/password/change/confirm", {
              body: {
                challenge_id: ch.challenge_id,
                code,
                new_password: String(form.get("new_password") ?? ""),
              },
            }),
          );
          setDone(true);
        }}
      />
    );
  }
  return (
    <StartForm
      field={
        <Field
          label={t("auth.currentPassword")}
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
          maxLength={256}
        />
      }
      submitLabel={t("auth.security.submit")}
      start={async (form) =>
        unwrap(
          await api().identity.POST("/v1/auth/password/change", {
            body: { current_password: String(form.get("current_password") ?? "") },
          }),
        )
      }
      onChallenge={setChallenge}
    />
  );
}
