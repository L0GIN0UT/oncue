/**
 * Состояние экрана в адресе: вкладка живёт в `?tab=`, чтобы переживать перезагрузку и ссылку.
 * Адрес меняется на месте (`replaceState`, без новой записи в истории и без перезапроса страницы).
 * `null` или значение по умолчанию убирает параметр.
 */
export function setUrlParam(name: string, value: string | null): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (value === null) url.searchParams.delete(name);
  else url.searchParams.set(name, value);
  window.history.replaceState(window.history.state, "", url);
}
