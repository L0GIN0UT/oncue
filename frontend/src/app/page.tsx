import { fetchHomePage } from "@/shared/api/server";
import { TodayGame } from "@/features/daily/TodayGame";

/**
 * Главная — сегодняшняя игра (дейлик), а не лендинг. Музыка по умолчанию, кино — `?domain=film`
 * из меню. SSR отдаёт каркас и публичные блоки bff, личный статус догружает клиент (D2).
 */
export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ domain?: string | string[] }>;
}) {
  const [{ domain }, page] = await Promise.all([searchParams, fetchHomePage()]);
  return <TodayGame initial={page} domain={domain === "film" ? "film" : "music"} />;
}
