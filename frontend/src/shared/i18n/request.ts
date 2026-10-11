/** Конфигурация next-intl без маршрутизации по локали: на этапе 2 только `ru`. */
import { getRequestConfig } from "next-intl/server";
import messages from "../../../messages/ru.json";
import { LOCALE, TIME_ZONE } from "./config";

export default getRequestConfig(async () => ({
  locale: LOCALE,
  timeZone: TIME_ZONE,
  messages,
}));
