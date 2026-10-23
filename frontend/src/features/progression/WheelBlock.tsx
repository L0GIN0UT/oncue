"use client";
/**
 * Колесо на профиле: секторы и шансы — из ответа progression, кнопка «Крутить» шлёт `POST
 * /v1/wheel/spin` прямо в progression. Сектор выбирает сервер, поэтому анимация (плавная подсветка
 * выпавшего сектора, без `prefers-reduced-motion`) на результат не влияет. Спин идемпотентен:
 * `repeated = true` — «уже крутили сегодня», обрыв сети можно повторить безопасно.
 */
import { useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
import { ApiError, NetworkError } from "@/shared/api/errors";
import type { PrizeCard, SpinResult, WheelCard, WheelSectorCard } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { cn } from "@/shared/lib/cn";
import { homeQueryKey } from "@/features/daily/DailyCards";
import { profileQueryKey, spinWheel } from "./api";
import { chancePercent, dayRelation, spinOutcome } from "./logic";

type SpinError = "auth" | "forbidden" | "conflict" | "network" | "other";

function errorKind(e: unknown): SpinError {
  if (e instanceof NetworkError) return "network";
  if (e instanceof ApiError) {
    // 401 дошёл сюда уже после refresh в apiFetch: сессии нет — нужен вход.
    if (e.status === 401) return "auth";
    if (e.status === 403) return "forbidden";
    if (e.status === 409) return "conflict";
  }
  return "other";
}

const ERROR_KEYS = {
  auth: "progression.wheel.errors.auth",
  forbidden: "progression.wheel.errors.forbidden",
  conflict: "progression.wheel.errors.conflict",
  network: "progression.wheel.errors.network",
  other: "progression.wheel.errors.other",
} as const satisfies Record<SpinError, string>;

/** Приз без названия сектора: тип и параметры из данных (запасной текст). */
function usePrizeText() {
  const t = useTranslations();
  const f = useFormatter();
  return (prize: PrizeCard): string => {
    switch (prize.kind) {
      case "xp":
        return t("progression.prize.xp", { amount: f.number(prize.amount) });
      case "verses":
        return t("progression.prize.verses", { amount: f.number(prize.amount) });
      case "boost":
        return t("progression.prize.boost", {
          multiplier: f.number(prize.multiplier ?? 1, { maximumFractionDigits: 2 }),
          matches: prize.matches ?? 0,
        });
      case "item":
        return t("progression.prize.item");
      default:
        return t("progression.prize.none");
    }
  };
}

export function WheelBlock({ wheel }: { wheel: WheelCard }) {
  const t = useTranslations();
  const f = useFormatter();
  const client = useQueryClient();
  const prizeText = usePrizeText();
  const [result, setResult] = useState<SpinResult | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<SpinError | null>(null);

  const shown = result ?? wheel.today ?? null;
  const sector = shown ? wheel.sectors.find((s) => s.key === shown.sector_key) : undefined;
  const outcome = result ? spinOutcome(result) : null;

  // Замок от двойного клика: `pending` обновится только после рендера, а второй клик может
  // прийти раньше.
  const lock = useRef(false);
  // После успешного спина кнопка заблокирована по локальному результату, даже если обновить
  // профиль не удалось.
  const spun = !wheel.can_spin || result !== null;

  const spin = async () => {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      setResult(await spinWheel());
    } catch (e) {
      setError(errorKind(e));
      return;
    } finally {
      lock.current = false;
      setPending(false);
    }
    await Promise.allSettled([
      client.invalidateQueries({ queryKey: profileQueryKey }),
      client.invalidateQueries({ queryKey: homeQueryKey }),
    ]);
  };

  const next = wheel.next_spin_at ? new Date(wheel.next_spin_at) : null;
  const when = next ? dayRelation(next, new Date()) : null;
  const time = next ? f.dateTime(next, { hour: "2-digit", minute: "2-digit" }) : "";

  if (!wheel.enabled) {
    return (
      <p className="text-ink-muted" data-testid="wheel-disabled">
        {t("progression.wheel.disabled")}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <SectorList sectors={wheel.sectors} highlight={shown?.sector_key ?? null} />
      <div className="flex flex-col items-start gap-3">
        <Button
          onClick={() => void spin()}
          disabled={spun || pending}
          aria-busy={pending}
          data-testid="wheel-spin"
        >
          {pending ? t("progression.wheel.spinning") : t("progression.wheel.spin")}
        </Button>
        {spun && !pending && (
          <p className="text-sm text-ink-muted" data-testid="wheel-spun">
            {t("progression.wheel.alreadySpun")}
            {next &&
              ` ${
                when === "today"
                  ? t("progression.wheel.nextSpin", { time })
                  : when === "tomorrow"
                    ? t("progression.wheel.nextSpinTomorrow", { time })
                    : t("progression.wheel.nextSpinDate", {
                        date: f.dateTime(next, { day: "numeric", month: "long" }),
                        time,
                      })
              }`}
          </p>
        )}
        <div aria-live="polite" className="flex flex-col gap-1" data-testid="wheel-feedback">
          {shown && (
            <p className="font-medium" data-testid="wheel-result">
              {outcome === "repeated" && (
                <span className="block text-sm font-normal text-ink-muted">
                  {t("progression.wheel.repeated")}
                </span>
              )}
              {t("progression.wheel.result", {
                prize: sector ? sector.title : prizeText(shown.prize),
              })}
            </p>
          )}
          {error && (
            <p className="text-sm text-ink-muted" role="alert" data-testid="wheel-error">
              {t(ERROR_KEYS[error])}
              {(error === "auth" || error === "forbidden") && (
                <>
                  {" "}
                  <Link
                    href={error === "auth" ? "/login" : "/register"}
                    className="underline underline-offset-4"
                  >
                    {error === "auth" ? t("nav.login") : t("auth.signUp")}
                  </Link>
                </>
              )}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function SectorList({
  sectors,
  highlight,
}: {
  sectors: WheelSectorCard[];
  highlight: string | null;
}) {
  const t = useTranslations();
  const prizeText = usePrizeText();
  return (
    <ul className="grid gap-2 sm:grid-cols-2" data-testid="wheel-sectors">
      {sectors.map((s) => (
        <li
          key={s.key}
          data-testid={`sector-${s.key}`}
          data-hit={s.key === highlight}
          className={cn(
            "flex items-baseline justify-between gap-2 rounded-lg bg-night px-3 py-2 text-sm ring-1 ring-rule",
            "transition-[background-color,box-shadow] duration-500 motion-reduce:transition-none",
            s.key === highlight && "ring-2 ring-[var(--accent,var(--color-music))]",
          )}
        >
          <span dir="auto" title={prizeText(s.prize)}>
            {s.title}
          </span>
          <span className="tabular text-ink-muted">
            {t("progression.wheel.chance", { percent: chancePercent(s.chance) })}
          </span>
        </li>
      ))}
    </ul>
  );
}
