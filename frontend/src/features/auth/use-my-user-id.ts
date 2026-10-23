import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/shared/api/client";
import { useViewer } from "./session";

/**
 * Мой `user_id` (identity `/v1/me`): по нему экран находит свой слот, права хоста и соперников.
 * Только пользователю — гостю онлайн недоступен (D13), запрос не уходит.
 */
export function useMyUserId(): string | null {
  const viewer = useViewer();
  const { data } = useQuery({
    queryKey: ["identity", "me"],
    queryFn: async () => unwrap(await api().identity.GET("/v1/me")),
    enabled: viewer.kind === "user",
    staleTime: 5 * 60_000,
  });
  return data?.id ?? null;
}
