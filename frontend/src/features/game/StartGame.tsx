"use client";
/**
 * Старт или возобновление сессии режима, затем `replace` на `/play/s/{id}`.
 * Без сессии сначала выдаётся гостевая (D5). Пройденный набор периода (дейлик, недельная сетка,
 * 409 `game.daily_already_played`) ведёт на итог `details.session_id`.
 *
 * Что выбрать перед стартом, говорит режим (`start` из `GET /v1/modes`, D12): `start.subject` —
 * сначала выбор персон (марафон), иначе сессия стартует сразу.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { ModeOut, SessionSnapshot } from "@/shared/api/types";
import { Button } from "@/shared/ui/button";
import { newIdempotencyKey } from "@/shared/lib/id";
import { withSession } from "@/features/auth/session";
import { SubjectPicker } from "@/features/solo/SubjectPicker";

type Failure =
  "guest_not_allowed" | "daily_not_ready" | "mode_not_found" | "rate_limited" | "failed";

export function failureOf(e: unknown): Failure {
  if (isApiError(e, "game.guest_not_allowed")) return "guest_not_allowed";
  if (isApiError(e, "game.daily_not_ready")) return "daily_not_ready";
  if (isApiError(e, "game.mode_not_found")) return "mode_not_found";
  if (isApiError(e) && e.status === 429) return "rate_limited";
  return "failed";
}

/** Сессия уже сыграна в этом периоде — id итога из `details` 409 `game.daily_already_played`. */
export function playedSessionOf(e: unknown): string | null {
  const id = isApiError(e, "game.daily_already_played") ? e.details?.session_id : null;
  return typeof id === "string" && id ? id : null;
}

export const modesQueryKey = ["modes", "all"] as const;

/** Все опубликованные режимы (что выбирать перед стартом — `start`). */
export function useAllModes() {
  return useQuery({
    queryKey: modesQueryKey,
    queryFn: async (): Promise<ModeOut[]> => unwrap(await api().game.GET("/v1/modes")),
    staleTime: 5 * 60_000,
    retry: 1,
  });
}

type StartBody = { subjectIds?: string[] };

function useStartSession(modeKey: string, presetId: string | null) {
  const router = useRouter();
  const [failure, setFailure] = useState<Failure | null>(null);
  const [pending, setPending] = useState(false);
  const key = useRef(newIdempotencyKey());

  const start = useCallback(
    async ({ subjectIds }: StartBody = {}) => {
      setPending(true);
      setFailure(null);
      try {
        const s = await withSession(async () =>
          unwrap<SessionSnapshot>(
            await api().game.POST("/v1/sessions", {
              params: { header: { "Idempotency-Key": key.current } },
              body: {
                mode_key: modeKey,
                preset_id: presetId,
                ...(subjectIds?.length ? { subject_ids: subjectIds } : {}),
              },
            }),
          ),
        );
        router.replace(`/play/s/${s.id}`);
      } catch (e) {
        const played = playedSessionOf(e);
        if (played) router.replace(`/results/${played}`);
        else {
          // Повтор после ошибки — новый ход: прежний ключ сервер мог запомнить с ошибкой.
          key.current = newIdempotencyKey();
          setFailure(failureOf(e));
          setPending(false);
        }
      }
    },
    [modeKey, presetId, router],
  );
  return { start, failure, pending, clearFailure: () => setFailure(null) };
}

export function StartGame({ modeKey, presetId }: { modeKey: string; presetId: string | null }) {
  const t = useTranslations();
  const modes = useAllModes();
  const { start, failure, pending, clearFailure } = useStartSession(modeKey, presetId);
  const mode = modes.data?.find((m) => m.key === modeKey);
  const subject = mode?.start?.subject ?? null;
  // Список режимов не пришёл — стартуем без выбора: сервер сам ответит, если выбор нужен.
  const decided = modes.isSuccess || modes.isError;
  const auto = decided && !subject;
  const started = useRef(false);

  useEffect(() => {
    if (!auto || started.current) return;
    started.current = true;
    void start();
  }, [auto, start]);

  if (failure) {
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
                clearFailure();
                if (auto) void start();
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

  if (mode && subject) {
    return (
      <SubjectPicker
        mode={mode}
        roles={subject.roles}
        max={subject.max}
        pending={pending}
        onStart={(subjectIds) => void start({ subjectIds })}
      />
    );
  }

  return (
    <p className="pt-16 text-center text-ink-muted" role="status">
      {t("start.loading")}
    </p>
  );
}
