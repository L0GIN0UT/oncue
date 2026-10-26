/** Календарь активности профиля: текущий месяц, дни серии дейлика заполнены. */

export type DayState = "played" | "today" | "missed" | "future";
export type CalendarDay = { day: number; state: DayState };
export type MonthGrid = {
  /** Пустых клеток перед первым числом (неделя с понедельника). */
  leading: number;
  days: CalendarDay[];
};

const DAY_MS = 86_400_000;

/**
 * Сетка месяца по длине серии. Серия идёт подряд до вчера, а сегодняшний день засчитан, если
 * дейлик уже сыгран. Бэкенд отдаёт только длину серии, а не отметки по дням, поэтому дни вне
 * серии показаны как «не сыграно» (так же восстановлена неделя в меню, `shell/week.ts`).
 */
export function monthGrid(today: Date, streak: number, playedToday: boolean): MonthGrid {
  const year = today.getFullYear();
  const month = today.getMonth();
  const total = new Date(year, month + 1, 0).getDate();
  const leading = (new Date(year, month, 1).getDay() + 6) % 7;
  const todayStart = new Date(year, month, today.getDate()).getTime();
  const days: CalendarDay[] = [];
  for (let day = 1; day <= total; day++) {
    const back = Math.round((todayStart - new Date(year, month, day).getTime()) / DAY_MS);
    let state: DayState;
    if (back < 0) state = "future";
    else if (back === 0) state = playedToday ? "played" : "today";
    else if (streak > 0 && (playedToday ? streak > back : streak >= back)) state = "played";
    else state = "missed";
    days.push({ day, state });
  }
  return { leading, days };
}
