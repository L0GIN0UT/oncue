/**
 * Сессия браузера. `gv_access` — HttpOnly, поэтому признак сессии — cookie `gv_csrf`
 * (identity выдаёт её вместе с access при входе, refresh и гостевой сессии).
 */
import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { isApiError } from "@/shared/api/errors";
import type { SessionInfo } from "@/shared/api/types";
import { refreshSession } from "@/shared/api/refresh";
import { csrfToken } from "@/shared/lib/cookies";

export const sessionQueryKey = ["session"] as const;

/** Пауза перед единственным автоповтором, если имя пользователя не пришло, мс. */
export const NAME_RETRY_MS = 1_500;

/**
 * Пользователь без имени: identity на миг был недоступен, и bff отдал субъекта без
 * `display_name` (ответ при этом 200). Это сбой, а не состояние — имя вот-вот появится.
 */
export const isNameless = (data: SessionInfo | undefined): boolean =>
  data?.subject?.kind === "user" && !data.subject.display_name;

/** Анонимный ответ при живой cookie сессии: access отозван или просрочен, а refresh ещё жив. */
const lostSession = (data: SessionInfo | undefined): boolean =>
  data !== undefined && data.subject === null && csrfToken() !== null;

/**
 * Общие флаги «повтор уже был» живут в кеше запросов, а не в состоянии хука: `useSessionInfo`
 * зовут десятки экранов, и один сбой должен получить ровно один повтор на всё приложение, даже
 * если шапка перемонтировалась (игровой маршрут и обратно).
 */
const nameRetryKey = ["session", "name-retry"] as const;
const recoveryKey = ["session", "recovery"] as const;

function useSharedFlag(key: readonly string[]): boolean {
  const { data } = useQuery({
    queryKey: key,
    queryFn: () => false,
    enabled: false,
    initialData: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  return data;
}

/**
 * Кто играет: пользователь, гость или никто (bff `GET /v1/session`).
 *
 * Возврат на вкладку и сеть перезапрашивают сессию (по `staleTime`; у пользователя без имени он
 * нулевой). `nameResolving` — имя не пришло и повтор ещё не отработал: экран показывает
 * скелетон, а не «Игрок».
 *
 * С `{ keep: true }` (только шапка) хук сам лечит сбои, по одному разу на приложение: если имя
 * не пришло — повтор через {@link NAME_RETRY_MS}; если bff ответил «аноним» при живой cookie
 * сессии — refresh и повторный запрос.
 */
export function useSessionInfo({ keep = false }: { keep?: boolean } = {}) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: sessionQueryKey,
    queryFn: async (): Promise<SessionInfo> => unwrap(await api().web.GET("/v1/session")),
    staleTime: (q) => (isNameless(q.state.data) ? 0 : 60_000),
    refetchOnWindowFocus: true,
    refetchOnReconnect: true,
  });
  const { data, refetch, isFetching } = query;
  const nameless = isNameless(data);
  const lost = lostSession(data);
  const nameRetried = useSharedFlag(nameRetryKey);

  useEffect(() => {
    if (!keep) return;
    // Имя пришло / сессия вернулась — следующий сбой снова получит свой повтор.
    if (!nameless && client.getQueryData(nameRetryKey)) client.setQueryData(nameRetryKey, false);
    if (!lost && client.getQueryData(recoveryKey)) client.setQueryData(recoveryKey, false);
  }, [keep, client, nameless, lost]);

  useEffect(() => {
    if (!keep || !nameless || nameRetried) return;
    const timer = setTimeout(() => {
      if (client.getQueryData(nameRetryKey)) return; // другой экземпляр уже повторил
      void refetch(); // сначала запрос, потом флаг: между ними скелетон не гаснет
      client.setQueryData(nameRetryKey, true);
    }, NAME_RETRY_MS);
    return () => clearTimeout(timer);
  }, [keep, nameless, nameRetried, client, refetch]);

  useEffect(() => {
    const csrf = csrfToken();
    if (!keep || !lost || !csrf || client.getQueryData(recoveryKey)) return;
    client.setQueryData(recoveryKey, true);
    void refreshSession(csrf).then((ok) => {
      if (ok) void refetch();
    });
  }, [keep, lost, client, refetch]);

  return { ...query, nameResolving: nameless && (!nameRetried || isFetching) };
}

/**
 * Вид зрителя для экранов этапа 3b: создавать сборники, лайкать и вести медиатеку может только
 * пользователь (D7); гостю и анониму — ссылка на регистрацию. `pending` — сессия ещё грузится.
 */
export function useViewer(): { kind: "user" | "guest" | "anonymous"; pending: boolean } {
  const { data, isPending } = useSessionInfo();
  const kind = data?.subject?.kind ?? "anonymous";
  return { kind, pending: isPending };
}

/**
 * Начать гостевую сессию.
 *
 * 409 `auth.already_authenticated` — у identity есть живая сессия, но браузер, раз уж дошёл
 * сюда, не смог ею воспользоваться (например, исчезла cookie access). Это успех только после
 * успешного refresh; иначе ошибка уходит вызывающему (без бесконечного цикла 401 → 409).
 */
export async function startGuestSession(): Promise<void> {
  const result = await api().identity.POST("/v1/auth/guest");
  if (result.response.status === 409 && (await refreshSession(csrfToken()))) return;
  unwrap(result);
}

/** Перед стартом игры: без сессии — выдать гостевую (D5). */
export async function ensureSession(): Promise<void> {
  if (!csrfToken()) await startGuestSession();
}

/**
 * Восстановить сессию, которую сервер не принял: просроченный access при живой cookie
 * `gv_csrf` лечится refresh-ом, без cookie или при неудачном refresh выдаётся гостевая.
 *
 * Нужна там, где 401 до браузера не доходит: bff отвечает 200 и прячет отказ game в блоке
 * страницы (`unavailable`), поэтому общий refresh на 401 в `apiFetch` не срабатывает.
 */
export async function recoverSession(): Promise<void> {
  const csrf = csrfToken();
  if (csrf && (await refreshSession(csrf))) return;
  await startGuestSession();
}

/**
 * Выполнить действие, требующее сессии. Если сессия оказалась недействительной (401 после
 * неудачного refresh), выдаётся гостевая и действие повторяется один раз.
 */
export async function withSession<T>(action: () => Promise<T>): Promise<T> {
  await ensureSession();
  try {
    return await action();
  } catch (e) {
    if (!isApiError(e) || e.status !== 401) throw e;
    await startGuestSession();
    return action();
  }
}
