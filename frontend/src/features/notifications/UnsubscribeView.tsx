"use client";
/**
 * Отписка по ссылке из письма `/unsubscribe?token=...` — без входа. Токен в адресе —
 * чувствительное значение, поэтому:
 * - страница клиентская: сервер Next не читает `searchParams`, токен не попадает в его рендер;
 * - токен читается из адреса один раз и сразу убирается `history.replaceState`;
 * - дальше он живёт только в памяти компонента и уходит в теле `POST /v1/unsubscribe`;
 * - запрос уходит ТОЛЬКО по клику «Отписаться»: почтовые сканеры открывают ссылки сами и не
 *   должны отписывать пользователя без его действия;
 * - никаких `console.*`, аналитики и текстов ошибок с токеном;
 * - `Referrer-Policy: no-referrer` задаёт страница (см. `app/unsubscribe`).
 * Результат нейтральный: любой 422 (чужой, просроченный, неверный токен) — одно сообщение.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ApiError } from "@/shared/api/errors";
import { Button } from "@/shared/ui/button";
import { unsubscribe } from "./api";

type State = "checking" | "ready" | "working" | "done" | "invalid" | "later";

const TOKEN_MIN = 16;
const TOKEN_MAX = 512;
const TOKEN_CHARS = /^[A-Za-z0-9_-]+$/;

/**
 * Правдоподобность токена (непрочитанная ссылка, а не проверка подписи): непрозрачная строка
 * `[A-Za-z0-9_-]`, 16–512 символов. Формат токена может меняться, поэтому проверка мягкая;
 * настоящая проверка — на сервере.
 */
export function isPlausibleToken(token: string | null): token is string {
  return (
    token !== null &&
    token.length >= TOKEN_MIN &&
    token.length <= TOKEN_MAX &&
    TOKEN_CHARS.test(token)
  );
}

/** Прочитать токен из адреса и тут же убрать его (и весь query) из истории. */
function takeToken(): string | null {
  const token = new URLSearchParams(window.location.search).get("token");
  window.history.replaceState(window.history.state, "", window.location.pathname);
  return token;
}

function outcome(e: unknown): State {
  // 4xx (кроме 429) — токен не подошёл, одно сообщение на все причины; 429/5xx/сеть — повтор.
  if (e instanceof ApiError && e.status !== 429 && e.status < 500) return "invalid";
  return "later";
}

export function UnsubscribeView() {
  const t = useTranslations();
  const [state, setState] = useState<State>("checking");
  const token = useRef<string | null>(null);
  const started = useRef(false);
  const lock = useRef(false);

  useEffect(() => {
    // StrictMode запускает эффект дважды: токен читаем один раз (второй раз адрес уже чист).
    if (started.current) return;
    started.current = true;
    const taken = takeToken();
    token.current = isPlausibleToken(taken) ? taken : null;
    // Адрес браузера читается один раз после монтирования (на сервере его нет) — это и есть
    // синхронизация с внешней системой; состояние зависит от прочитанного токена.
    setState(token.current ? "ready" : "invalid");
  }, []);

  const send = useCallback(async () => {
    if (!token.current || lock.current) return;
    lock.current = true;
    setState("working");
    try {
      await unsubscribe(token.current);
      token.current = null;
      setState("done");
    } catch (e) {
      setState(outcome(e));
    } finally {
      lock.current = false;
    }
  }, []);

  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12">
      <h1 className="font-display text-2xl font-bold">{t("unsubscribe.title")}</h1>
      <p
        role="status"
        aria-live="polite"
        data-testid="unsubscribe-result"
        data-state={state}
        className="text-ink-muted"
      >
        {state === "checking" && t("common.loading")}
        {state === "ready" && t("unsubscribe.ready")}
        {state === "working" && t("unsubscribe.working")}
        {state === "done" && t("unsubscribe.done")}
        {state === "invalid" && t("unsubscribe.invalid")}
        {state === "later" && t("unsubscribe.later")}
      </p>
      {state === "ready" && (
        <Button onClick={() => void send()} data-testid="unsubscribe-confirm">
          {t("unsubscribe.confirm")}
        </Button>
      )}
      {state === "later" && (
        <Button variant="secondary" onClick={() => void send()}>
          {t("unsubscribe.retry")}
        </Button>
      )}
      {state !== "checking" && state !== "working" && state !== "ready" && (
        <Link href="/" className="underline underline-offset-4">
          {t("unsubscribe.home")}
        </Link>
      )}
    </div>
  );
}
