/** Серия за неделю для меню: семь последних дней, заканчивая сегодняшним. */

export type WeekCell = { day: number; state: "played" | "today" | "missed" };

/**
 * Клетки недели по длине серии. Серия идёт подряд до вчера, а сегодняшний день засчитан, если
 * дейлик уже сыгран. Дни берутся по локальному календарю (`today` задаёт вызывающий).
 * Бэкенд отдаёт только длину серии, а не отметки по дням, поэтому клетки восстановлены по ней.
 */
export function weekCells(today: Date, streak: number, playedToday: boolean): WeekCell[] {
  const cells: WeekCell[] = [];
  for (let back = 6; back >= 0; back--) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back);
    const isToday = back === 0;
    // Сколько дней серии лежит позади этой клетки, считая её саму: сегодня — первая, если сыграно.
    const covered = playedToday ? streak > back : streak >= back;
    const state: WeekCell["state"] = isToday
      ? playedToday
        ? "played"
        : "today"
      : covered && streak > 0
        ? "played"
        : "missed";
    cells.push({ day: d.getDate(), state });
  }
  return cells;
}
