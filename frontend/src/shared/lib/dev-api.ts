/**
 * Обход gateway для e2e на моках: `/api/*` и `/media/*` переписываются на `GV_DEV_API_ORIGIN`.
 *
 * Включается только вместе с `GV_E2E=1`: случайно оставленная в проде переменная иначе
 * отправила бы cookie сессии на посторонний адрес. Переменная без флага — ошибка запуска.
 */

type Env = Record<string, string | undefined>;

export class DevApiConfigError extends Error {
  constructor() {
    super(
      "GV_DEV_API_ORIGIN задан без GV_E2E=1: переписывание /api/* в обход gateway разрешено " +
        "только в e2e на моках. Уберите GV_DEV_API_ORIGIN или задайте GV_E2E=1 (только для тестов).",
    );
    this.name = "DevApiConfigError";
  }
}

/** Адрес мок-бэкенда, если обход разрешён; `null` — обхода нет. */
export function devApiOrigin(env: Env = process.env): string | null {
  const origin = env.GV_DEV_API_ORIGIN?.trim();
  if (!origin) return null;
  if (env.GV_E2E !== "1") throw new DevApiConfigError();
  return origin;
}
