/**
 * Прогрессия (этап 5a). Экраны профиля — через bff (`/v1/pages/profile`, `/v1/pages/players/{id}`,
 * только чтение); спин колеса — мутация прямо в progression через gateway (`/api/progression/v1/
 * wheel/spin`, ADR 0017: команды идут в сервис, а не через bff). Спин без тела и идемпотентен по
 * естественному ключу (игрок, сутки): сектор выбирает только сервер (ADR 0044), поэтому
 * `Idempotency-Key` не нужен — повтор вернёт тот же результат с `repeated = true`.
 */
import { api, unwrap } from "@/shared/api/client";
import type { PlayerPage, ProfilePage, SpinResult } from "@/shared/api/types";

export const profileQueryKey = ["page", "profile"] as const;
export const playerQueryKey = (id: string) => ["page", "player", id] as const;

export async function fetchProfilePage(): Promise<ProfilePage> {
  return unwrap(await api().web.GET("/v1/pages/profile"));
}

export async function fetchPlayerPage(userId: string): Promise<PlayerPage> {
  return unwrap(
    await api().web.GET("/v1/pages/players/{user_id}", { params: { path: { user_id: userId } } }),
  );
}

/** Крутить колесо. `repeated = true` — за эти сутки уже крутили, вернулся прежний результат. */
export async function spinWheel(): Promise<SpinResult> {
  return unwrap(await api().progression.POST("/v1/wheel/spin"));
}
