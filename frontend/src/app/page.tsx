import { fetchHomePage } from "@/shared/api/server";
import { HomeView } from "@/features/home/HomeView";
import { EventSpot } from "@/features/events/EventSpot";

/**
 * Главная — хаб (макет v3): дейлики, прогресс, лента дейлика и входы в разделы. Игра дейлика
 * идёт на `/play/<mode_key>`. SSR отдаёт публичные блоки bff, личный статус догружает клиент.
 * `EventSpot` остаётся: на главной лежит находка сезонного события (`ui_spot`, page_key `home`).
 */
export default async function HomePage() {
  const page = await fetchHomePage();
  return (
    <>
      <HomeView initial={page} />
      <EventSpot pageKey="home" />
    </>
  );
}
