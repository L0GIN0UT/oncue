/** Проба живости контейнера `web` (HEALTHCHECK в web.Dockerfile). Без обращения к бэкенду. */
export const dynamic = "force-dynamic";

export function GET(): Response {
  return new Response("ok\n", {
    headers: { "Content-Type": "text/plain", "Cache-Control": "no-store" },
  });
}
