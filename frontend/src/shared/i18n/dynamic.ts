/**
 * Перевод ключа, пришедшего с сервера (`title_key` режима, пресета): такие ключи не известны
 * при сборке. Неизвестный ключ не ломает экран — показывается запасной текст.
 */
import { useTranslations } from "next-intl";

export function useDynamicT(): (key: string, fallback: string) => string {
  const t = useTranslations();
  return (key, fallback) => {
    const k = key as Parameters<typeof t>[0];
    return t.has(k) ? t(k) : fallback;
  };
}
