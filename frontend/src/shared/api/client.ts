/** Клиенты API для браузера (openapi-fetch по сгенерированным типам). Пути — через gateway. */
import createClient, { type Client } from "openapi-fetch";
import type { paths as GamePaths } from "./schema/game";
import type { paths as BffPaths } from "./schema/bff_web";
import type { paths as CatalogPaths } from "./schema/catalog";
import type { paths as IdentityPaths } from "./schema/identity";
import type { paths as CollectionsPaths } from "./schema/collections";
import type { paths as LibraryPaths } from "./schema/library";
import type { paths as MatchPaths } from "./schema/match";
import { ApiError, parseRetryAfter } from "./errors";
import { apiFetch } from "./http";

export const API_PREFIX = {
  game: "/api/game",
  web: "/api/web",
  catalog: "/api/catalog",
  identity: "/api/identity",
  collections: "/api/collections",
  library: "/api/library",
  match: "/api/match",
} as const;

type Clients = {
  game: Client<GamePaths>;
  web: Client<BffPaths>;
  catalog: Client<CatalogPaths>;
  identity: Client<IdentityPaths>;
  collections: Client<CollectionsPaths>;
  library: Client<LibraryPaths>;
  match: Client<MatchPaths>;
};

let clients: Clients | null = null;

/** Клиенты создаются лениво: базовый адрес — origin страницы (тот же, что у gateway). */
export function api(): Clients {
  if (!clients) {
    const origin = globalThis.location.origin;
    const make = <P extends object>(prefix: string) =>
      createClient<P>({ baseUrl: origin + prefix, fetch: apiFetch });
    clients = {
      game: make<GamePaths>(API_PREFIX.game),
      web: make<BffPaths>(API_PREFIX.web),
      catalog: make<CatalogPaths>(API_PREFIX.catalog),
      identity: make<IdentityPaths>(API_PREFIX.identity),
      collections: make<CollectionsPaths>(API_PREFIX.collections),
      library: make<LibraryPaths>(API_PREFIX.library),
      match: make<MatchPaths>(API_PREFIX.match),
    };
  }
  return clients;
}

/** Сбросить кэш клиентов (тесты). */
export function resetApiClients(): void {
  clients = null;
}

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Данные успешного ответа или {@link ApiError} из Problem Details. */
export function unwrap<T>(result: Result<T>): T {
  const { data, error, response } = result;
  if (error !== undefined || !response.ok) {
    const problem = error && typeof error === "object" ? (error as Record<string, unknown>) : null;
    throw new ApiError(
      response.status,
      problem as ConstructorParameters<typeof ApiError>[1],
      parseRetryAfter(response),
    );
  }
  return data as T;
}
