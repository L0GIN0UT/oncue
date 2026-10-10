"use client";
/**
 * Старт или возобновление сессии режима, затем `replace` на `/play/s/{id}`.
 * Без сессии сначала выдаётся гостевая (D5). Пройденный дейлик (409) ведёт на итог.
 */
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { SessionSnapshot } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { newIdempotencyKey } from "@/shared/lib/id";
import { withSession } from "@/features/auth/session";

type Failure =
  "guest_not_allowed" | "daily_not_ready" | "mode_not_found" | "rate_limited" | "failed";

export function failureOf(e: unknown): Failure {
  if (isApiError(e, "game.guest_not_allowed")) return "guest_not_allowed";
  if (isApiError(e, "game.daily_not_ready")) return "daily_not_ready";
  if (isApiError(e, "game.mode_not_found")) return "mode_not_found";
  if (isApiError(e) && e.status === 429) return "rate_limited";
  return "failed";
}

export function StartGame({ modeKey, presetId }: { modeKey: string; presetId: string | null }) {
  const t = useTranslations();
  const router = useRouter();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [attempt, setAttempt] = useState(0);
  const started = useRef<number | null>(null);
  const key = useRef(newIdempotencyKey());

  useEffect(() => {
    if (started.current === attempt) return;
    started.current = attempt;
    withSession(async () =>
      unwrap<SessionSnapshot>(
        await api().game.POST("/v1/sessions", {
          params: { header: { "Idempotency-Key": key.current } },
          body: { mode_key: modeKey, preset_id: presetId },
        }),
      ),
    )
      .then((s) => router.replace(`/play/s/${s.id}`))
      .catch((e: unknown) => {
        const played = isApiError(e, "game.daily_already_played") ? e.details?.session_id : null;
        if (typeof played === "string") router.replace(`/results/${played}`);
        else setFailure(failureOf(e));
      });
  }, [attempt, modeKey, presetId, router]);

  if (!failure) {
    return (
      <p className="pt-16 text-center text-ink-muted" role="status">
        {t("start.loading")}
      </p>
    );
  }
  return (
    <div className="flex max-w-xl flex-col items-start gap-4 pt-12" role="alert">
      <h1 className="font-display text-2xl font-bold">{t(`start.error.${failure}.title`)}</h1>
      <p className="text-ink-muted">{t(`start.error.${failure}.text`)}</p>
      <div className="flex flex-wrap gap-3">
        {failure === "guest_not_allowed" ? (
          <Button asChild>
            <Link href={`/login?next=${encodeURIComponent(`/play/${modeKey}`)}`}>
              {t("nav.login")}
            </Link>
          </Button>
        ) : failure !== "mode_not_found" ? (
          <Button
            onClick={() => {
              key.current = newIdempotencyKey();
              setFailure(null);
              setAttempt((n) => n + 1);
            }}
          >
            {t("common.retry")}
          </Button>
        ) : null}
        <Button asChild variant="secondary">
          <Link href="/solo">{t("start.toSolo")}</Link>
        </Button>
      </div>
    </div>
  );
}
