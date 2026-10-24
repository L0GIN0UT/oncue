"use client";
/**
 * Ввод промокода в профиле: `POST /v1/promo/redeem` прямо в progression с `Idempotency-Key`.
 * Неверный, просроченный и исчерпанный код дают один и тот же 404 — и одно сообщение (причину
 * перебору не раскрываем). 429 — обратный отсчёт по `Retry-After`. Ключ меняется после каждого
 * завершённого ответа; после обрыва сети повтор идёт с тем же ключом.
 */
import { useRef, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { ApiError, NetworkError } from "@/shared/api/errors";
import type { PromoRedeemResult } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { Field } from "@/shared/ui/field";
import { newIdempotencyKey, redeemPromo } from "@/features/shop/api";
import { shopQueryKey } from "@/features/shop/api";
import { profileQueryKey } from "./api";
import { useCountdown } from "./use-countdown";

type Failure = "empty" | "invalid" | "rateLimited" | "guest" | "network" | "other";

const FAILURE_KEYS = {
  empty: "promo.empty",
  invalid: "promo.invalid",
  rateLimited: "promo.rateLimitedShort",
  guest: "promo.guest",
  network: "promo.network",
  other: "promo.other",
} as const satisfies Record<Failure, string>;

export function PromoBlock() {
  const t = useTranslations();
  const client = useQueryClient();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [result, setResult] = useState<PromoRedeemResult | null>(null);
  const { remaining, start } = useCountdown();
  const key = useRef<string | null>(null);
  const lock = useRef(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const value = code.trim();
    if (!value) {
      setFailure("empty");
      setResult(null);
      return;
    }
    if (lock.current || remaining > 0) return;
    lock.current = true;
    setBusy(true);
    setFailure(null);
    setResult(null);
    key.current ??= newIdempotencyKey();
    try {
      const res = await redeemPromo(value, key.current);
      key.current = null;
      setResult(res);
      setCode("");
      await Promise.allSettled([
        client.invalidateQueries({ queryKey: profileQueryKey }),
        client.invalidateQueries({ queryKey: shopQueryKey }),
      ]);
    } catch (err) {
      if (err instanceof NetworkError) {
        // Ключ сохраняется: повтор не задвоит награду.
        setFailure("network");
      } else if (err instanceof ApiError) {
        // 4xx — запрос отклонён целиком, можно начинать заново; 5xx мог дойти до выдачи награды,
        // повтор идёт с тем же ключом.
        if (err.status >= 400 && err.status < 500) key.current = null;
        if (err.status === 404) setFailure("invalid");
        else if (err.status === 429) {
          setFailure("rateLimited");
          if (err.retryAfterS) start(err.retryAfterS);
        } else if (err.status === 403) setFailure("guest");
        else setFailure("other");
      } else {
        setFailure("other");
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  const reward = result
    ? result.reward_kind === "item" && result.item
      ? t("promo.rewardItem", { title: result.item.title })
      : t("promo.rewardVerses", { amount: result.amount ?? 0 })
    : null;

  return (
    <form onSubmit={(e) => void submit(e)} className="flex flex-col gap-3" noValidate>
      <Field
        label={t("promo.label")}
        hint={t("promo.hint")}
        value={code}
        onChange={(e) => {
          setCode(e.target.value);
          // Другой код — другой запрос, другой ключ идемпотентности.
          key.current = null;
        }}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        maxLength={64}
        data-testid="promo-input"
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          disabled={busy || remaining > 0}
          aria-busy={busy}
          data-testid="promo-submit"
        >
          {busy ? t("promo.busy") : t("promo.submit")}
        </Button>
        {remaining > 0 && (
          <p className="tabular text-sm text-ink-muted" data-testid="promo-countdown">
            {t("promo.rateLimited", { seconds: remaining })}
          </p>
        )}
      </div>
      <div aria-live="polite" className="flex flex-col gap-1">
        {failure && (
          <p role="alert" className="text-sm text-miss" data-testid="promo-error">
            {t(FAILURE_KEYS[failure])}
          </p>
        )}
        {reward && (
          <p className="text-sm text-exact" data-testid="promo-result">
            {result?.repeated ? `${t("promo.repeated")} ` : ""}
            {reward}
          </p>
        )}
      </div>
    </form>
  );
}
