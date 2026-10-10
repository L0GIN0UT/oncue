// Типизация ключей next-intl по messages/ru.json: опечатка в ключе — ошибка tsc.
import type messages from "../../../messages/ru.json";
import type { LOCALE } from "./config";

declare module "next-intl" {
  interface AppConfig {
    Locale: typeof LOCALE;
    Messages: typeof messages;
  }
}
