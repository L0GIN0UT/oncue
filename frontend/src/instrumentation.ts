/** Проверки конфигурации при старте сервера Next (до первого запроса). */
import { DevApiConfigError, devApiOrigin } from "@/shared/lib/dev-api";

export function register(): void {
  try {
    devApiOrigin();
  } catch (e) {
    // Next только логирует ошибку хука и продолжает работу — процесс завершаем сами, чтобы
    // контейнер с GV_DEV_API_ORIGIN без GV_E2E=1 не поднялся «наполовину».
    if (e instanceof DevApiConfigError && process.env.NEXT_RUNTIME === "nodejs") {
      console.error(e.message);
      process.exit(64);
    }
    throw e;
  }
}
