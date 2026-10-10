/** Ключ идемпотентности хода (uuid v4). Повтор запроса после сетевой ошибки — с тем же ключом. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}
